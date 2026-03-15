"""Text chunking utilities for RAG pipelines.

Uses LangChain's RecursiveCharacterTextSplitter under the hood — the industry
standard for chunk boundary selection.  Exposes a shared TextChunk model and
a ChunkingResource (ConfigurableResource) that surfaces chunk parameters in
the Dagster UI launchpad.

Supports per-document-type overrides so assets can route different content
types to optimal chunk sizes (e.g. short metadata docs stay atomic, dense
legal text gets larger chunks).
"""

from __future__ import annotations

import hashlib
import os

from dagster import ConfigurableResource
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field

from dagster_io.logging import get_logger
from dagster_io.metrics import CHUNKS_CREATED, CHUNK_PROCESSING_DURATION, track_duration

logger = get_logger(__name__)

DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


class TextChunk(BaseModel):
    """A chunk of text derived from a parent document."""

    chunk_id: str = Field(description="Unique chunk identifier (doc_id + index)")
    document_id: str = Field(description="Parent document ID")
    text: str = Field(description="Chunk text content")
    index: int = Field(description="Position within the parent document (0-based)")
    total_chunks: int = Field(description="Total chunks produced from parent document")
    metadata: dict = Field(default_factory=dict, description="Inherited + chunk-specific metadata")
    content_hash: str = Field(default="", description="SHA-256 of chunk text for dedup")

    def model_post_init(self, __context) -> None:
        if not self.content_hash and self.text:
            self.content_hash = hashlib.sha256(self.text.encode()).hexdigest()


class ChunkingResource(ConfigurableResource):
    """Configurable text chunking resource.

    All parameters are editable in the Dagster UI launchpad.  These serve as
    defaults; individual calls to ``chunk_document`` can override size/overlap
    for per-document-type optimization.

    Usage in assets::

        @asset
        def my_chunks(chunking: ChunkingResource, docs: list[Document]):
            # Use resource defaults
            chunks = chunking.chunk_document(doc_id, title, content)
            # Override for a specific doc type
            chunks = chunking.chunk_document(doc_id, title, content, chunk_size=2000)
            # Passthrough for short metadata docs
            chunks = chunking.passthrough(doc_id, title, content)
    """

    chunk_size: int = int(os.environ.get("CHUNK_SIZE", "1000"))
    chunk_overlap: int = int(os.environ.get("CHUNK_OVERLAP", "200"))
    prepend_title: bool = True

    def _splitter(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size or self.chunk_size,
            chunk_overlap=chunk_overlap or self.chunk_overlap,
            separators=DEFAULT_SEPARATORS,
            length_function=len,
        )

    def split_text(
        self,
        text: str,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> list[str]:
        """Split raw text into overlapping chunks."""
        if not text or not text.strip():
            return []
        return self._splitter(chunk_size, chunk_overlap).split_text(text)

    def chunk_document(
        self,
        document_id: str,
        title: str,
        content: str,
        metadata: dict | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> list[TextChunk]:
        """Chunk a document into TextChunk objects.

        Args:
            document_id: Parent document ID.
            title: Document title (prepended to each chunk if prepend_title=True).
            content: Full text content to split.
            metadata: Extra metadata to attach to each chunk.
            chunk_size: Override the resource default for this call.
            chunk_overlap: Override the resource default for this call.
        """
        size = chunk_size or self.chunk_size
        overlap = chunk_overlap or self.chunk_overlap
        logger.debug("Chunking document=%s size=%d overlap=%d content_len=%d", document_id, size, overlap, len(content))
        with track_duration(CHUNK_PROCESSING_DURATION, {"strategy": "recursive"}):
            raw_chunks = self.split_text(content, chunk_size=size, chunk_overlap=overlap)

        if not raw_chunks:
            return []

        total = len(raw_chunks)
        CHUNKS_CREATED.labels(strategy="recursive").inc(total)
        logger.info("Chunked document=%s into %d chunks (size=%d, overlap=%d)", document_id, total, size, overlap)
        base_meta = {
            **(metadata or {}),
            "chunk_size": size,
            "chunk_overlap": overlap,
            "strategy": "recursive",
        }

        return [
            TextChunk(
                chunk_id=f"{document_id}:chunk-{i}",
                document_id=document_id,
                text=f"{title}\n\n{text}" if (self.prepend_title and title) else text,
                index=i,
                total_chunks=total,
                metadata=base_meta,
            )
            for i, text in enumerate(raw_chunks)
        ]

    def passthrough(
        self,
        document_id: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> list[TextChunk]:
        """Wrap a short document as a single chunk without splitting.

        Use for metadata-only or very short documents (members, committees,
        offshore entities) where splitting would add noise.
        """
        text = content.strip()
        if not text:
            return []

        CHUNKS_CREATED.labels(strategy="passthrough").inc(1)
        logger.debug("Passthrough chunk document=%s len=%d", document_id, len(text))
        full_text = f"{title}\n\n{text}" if (self.prepend_title and title) else text
        return [
            TextChunk(
                chunk_id=f"{document_id}:chunk-0",
                document_id=document_id,
                text=full_text,
                index=0,
                total_chunks=1,
                metadata={**(metadata or {}), "strategy": "passthrough"},
            )
        ]


# ---------------------------------------------------------------------------
# Standalone helpers (for notebooks / non-Dagster usage)
# ---------------------------------------------------------------------------

def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    separators: list[str] | None = None,
) -> list[str]:
    """Split text into overlapping chunks via LangChain RecursiveCharacterTextSplitter."""
    if not text or not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=separators or DEFAULT_SEPARATORS,
        length_function=len,
    )
    return splitter.split_text(text)


def chunk_document(
    document_id: str,
    title: str,
    content: str,
    metadata: dict | None = None,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> list[TextChunk]:
    """Chunk a document into TextChunk objects (standalone, for notebooks)."""
    raw_chunks = chunk_text(content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not raw_chunks:
        return []

    total = len(raw_chunks)
    base_meta = {**(metadata or {}), "chunk_size": chunk_size, "chunk_overlap": chunk_overlap}

    return [
        TextChunk(
            chunk_id=f"{document_id}:chunk-{i}",
            document_id=document_id,
            text=f"{title}\n\n{text}" if title else text,
            index=i,
            total_chunks=total,
            metadata=base_meta,
        )
        for i, text in enumerate(raw_chunks)
    ]
