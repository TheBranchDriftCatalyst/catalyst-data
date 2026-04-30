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
from dataclasses import dataclass

from dagster import ConfigurableResource
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field

from dagster_io.logging import get_logger
from dagster_io.metrics import CHUNK_PROCESSING_DURATION, CHUNKS_CREATED, track_duration
from dagster_io.text import normalize_text

logger = get_logger(__name__)

DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


# ---------------------------------------------------------------------------
# Context-aware chunk sizing
# ---------------------------------------------------------------------------


@dataclass
class ChunkConfig:
    """Model-aware chunk sizing configuration.

    Computes a target chunk size from the model's context window minus
    prompt overhead and output reserve, then scales by ``context_fraction``.
    """

    model_context_tokens: int = 4096
    prompt_overhead_tokens: int = 1500  # system prompt + schema + few-shot
    output_reserve_tokens: int = 2000  # max expected extraction output
    context_fraction: float = 0.25  # use 25% of remaining context
    min_chunk_tokens: int = 100  # floor
    max_chunk_tokens: int = 8000  # ceiling (even for 128K models)
    overlap_tokens: int = 50
    strategy: str = "recursive"  # recursive|section|speaker|passthrough

    @property
    def target_tokens(self) -> int:
        available = self.model_context_tokens - self.prompt_overhead_tokens - self.output_reserve_tokens
        target = int(available * self.context_fraction)
        return max(self.min_chunk_tokens, min(target, self.max_chunk_tokens))

    @property
    def target_chars(self) -> int:
        return self.target_tokens * 4  # conservative estimate, refined by TokenCounter


class TokenCounter:
    """Count tokens using tiktoken when available, otherwise fall back to char/4."""

    def __init__(self, model: str = "gpt-4o"):
        self._encoder = None
        try:
            import tiktoken

            self._encoder = tiktoken.encoding_for_model(model)
        except (ImportError, KeyError):
            pass

    def count(self, text: str) -> int:
        if self._encoder:
            return len(self._encoder.encode(text))
        return len(text) // 4

    def chars_for_tokens(self, n_tokens: int) -> int:
        return n_tokens * 4  # approximate for char budget


PRESET_CONFIGS: dict[str, ChunkConfig] = {
    "small-local": ChunkConfig(model_context_tokens=4096),
    "medium-local": ChunkConfig(model_context_tokens=8192),
    "large-local": ChunkConfig(model_context_tokens=32768),
    "cloud-openai": ChunkConfig(model_context_tokens=128000),
    "cloud-anthropic": ChunkConfig(model_context_tokens=200000),
    "encoder": ChunkConfig(model_context_tokens=2048, context_fraction=0.5, max_chunk_tokens=512),
}


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
        # Normalize at ingestion — NFKC converts fullwidth chars, strips control chars
        title = normalize_text(title) if title else title
        content = normalize_text(content) if content else content

        size = chunk_size or self.chunk_size
        overlap = chunk_overlap or self.chunk_overlap
        logger.debug(
            "Chunking document=%s size=%d overlap=%d content_len=%d",
            document_id,
            size,
            overlap,
            len(content),
        )
        with track_duration(CHUNK_PROCESSING_DURATION, {"strategy": "recursive"}):
            raw_chunks = self.split_text(content, chunk_size=size, chunk_overlap=overlap)

        if not raw_chunks:
            return []

        total = len(raw_chunks)
        CHUNKS_CREATED.labels(strategy="recursive").inc(total)
        logger.info(
            "Chunked document=%s into %d chunks (size=%d, overlap=%d)",
            document_id,
            total,
            size,
            overlap,
        )
        base_meta = {
            **(metadata or {}),
            "chunk_size": size,
            "chunk_overlap": overlap,
            "strategy": "recursive",
        }

        # Compute character offsets so spans can map back to the original document
        full_text = f"{title}\n\n{content}" if (self.prepend_title and title) else content
        chunks = []
        for i, text in enumerate(raw_chunks):
            char_offset = full_text.find(text)
            chunk_meta = {
                **base_meta,
                "chunk_char_offset": char_offset if char_offset >= 0 else None,
            }
            chunks.append(
                TextChunk(
                    chunk_id=f"{document_id}:chunk-{i}",
                    document_id=document_id,
                    text=f"{title}\n\n{text}" if (self.prepend_title and title) else text,
                    index=i,
                    total_chunks=total,
                    metadata=chunk_meta,
                )
            )
        return chunks

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
        # Normalize at ingestion
        title = normalize_text(title) if title else title
        content = normalize_text(content) if content else content

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
    config: ChunkConfig | None = None,
) -> list[str]:
    """Split text into overlapping chunks via LangChain RecursiveCharacterTextSplitter.

    When *config* is provided, ``chunk_size`` and ``chunk_overlap`` are derived
    from the config (target_chars / overlap_tokens * 4) unless explicitly
    overridden by the caller.
    """
    if not text or not text.strip():
        return []

    if config is not None:
        chunk_size = config.target_chars
        chunk_overlap = config.overlap_tokens * 4

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
    base_meta = {
        **(metadata or {}),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
    }

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
