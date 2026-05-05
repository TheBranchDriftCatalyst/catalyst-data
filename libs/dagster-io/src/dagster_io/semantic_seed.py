"""SemanticChunkingSeed — stable per-chunk metadata for deterministic
seed sampling across the live corpus.

Every text-producing asset attaches one of these to each chunk it emits.
The seed travels with the chunk through the medallion (silver/gold) and
gets read back by the GT-candidate sampler so re-running the sampler
against the same corpus yields the same chunks regardless of when it
runs or which machine runs it.

## Why this exists

Today ``scripts/benchmark/sample_gt_candidates.py`` recomputes embeddings on every
invocation. That makes seed sampling expensive (embeds the whole corpus
each run) and non-deterministic across re-embeddings (different model
version → different vector → different "diverse" sample). Caching the
embedding next to the chunk fixes both.

## Resource convention

The seed embedder is registered in each code location's
``Definitions`` under the key ``embedding_seed`` — a separate
``EmbeddingResource`` instance from the production ``embedding`` /
``embeddings`` keys. They're decoupled because the production embedder
optimizes for downstream similarity quality (e.g. text-embedding-3-small,
768d, OpenAI-billed) while the seed embedder optimizes for cheap
diversity hashing (e.g. a small local sentence-transformer). The two
purposes wanted opposite trade-offs; one shared instance was a footgun.

Asset code asks for whichever purpose it needs:

    @asset
    def media_chunks(
        chunking: ChunkingResource,
        embedding_seed: EmbeddingResource,
        ...
    ) -> list[TextChunk]:
        chunks = chunking.chunk_document(...)
        return [
            attach_seed(chunk, embedding_seed, domain="media_ingest")
            for chunk in chunks
        ]

The exact embedding model behind ``embedding_seed`` is tracked in beads
issue CD-wnu5 — until that lands the helper accepts any
``EmbeddingResource`` and records the model it actually used so seeds
can be invalidated when the choice changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dagster_io.chunking import TextChunk
    from dagster_io.llm import EmbeddingResource


@dataclass(frozen=True)
class SemanticChunkingSeed:
    """Stable per-chunk metadata that drives deterministic GT seed sampling.

    Travels with the chunk through silver/gold so the sampler doesn't
    need to re-embed on every run. Pinned to the embedding model that
    produced the vector — when the model changes (CD-wnu5), seeds with
    the old ``embedding_model`` value can be filtered out and rebuilt.
    """

    chunk_id: str
    """The same chunk_id used everywhere else in the pipeline."""

    domain: str
    """Code-location domain — media_ingest | congress_data | open_leaks."""

    embedding: list[float]
    """Sentence embedding for the chunk's text (whole chunk, not per-token)."""

    embedding_model: str
    """Which model produced the vector. Used to invalidate stale seeds."""

    embedding_dimensions: int
    """Vector length — convenient sanity check for downstream consumers."""

    char_count: int
    """Length of the chunk text in characters."""

    semantic_hash: str
    """SHA-256 of the embedding (first 16 hex chars) — cheap LSH-style
    diversity bucket. Two chunks with the same hash are byte-identical
    embeddings; the sampler uses this to avoid sampling near-duplicates
    without having to compute pairwise cosine."""

    text_hash: str
    """SHA-256 of the chunk text (first 16 hex chars) — independent of
    the embedding model so we can detect "same text, different model"
    seeds and drop the stale ones cheaply."""

    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for JSONL persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SemanticChunkingSeed:
        """Round-trip from a persisted JSONL row."""
        return cls(**payload)


def _short_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


def build_seed(
    *,
    chunk_id: str,
    text: str,
    domain: str,
    embedding: list[float],
    embedding_model: str,
) -> SemanticChunkingSeed:
    """Construct a seed from a precomputed embedding.

    Most callers should use ``attach_seed`` instead — this lower-level
    factory exists for tests and for callers that batch their embedder
    calls separately from chunk emission.
    """
    embedding_bytes = ",".join(f"{v:.6f}" for v in embedding).encode("utf-8")
    return SemanticChunkingSeed(
        chunk_id=chunk_id,
        domain=domain,
        embedding=embedding,
        embedding_model=embedding_model,
        embedding_dimensions=len(embedding),
        char_count=len(text),
        semantic_hash=_short_hash(embedding_bytes),
        text_hash=_short_hash(text.encode("utf-8")),
    )


def attach_seed(
    chunk: TextChunk,
    embedding_seed: EmbeddingResource,
    *,
    domain: str,
) -> TextChunk:
    """Embed the chunk's text via the seed embedder and write the
    resulting ``SemanticChunkingSeed`` into the chunk's metadata under
    the key ``"semantic_seed"``.

    Returns the same chunk (for fluent chaining); ``TextChunk.metadata``
    is mutated in place.

    Single-chunk convenience wrapper. For batch emission prefer
    ``attach_seeds_batch`` so the embedder can amortize the API call /
    GPU step over many chunks.
    """
    [vector] = embedding_seed.embed([chunk.text])
    seed = build_seed(
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        domain=domain,
        embedding=vector,
        embedding_model=embedding_seed.model,
    )
    chunk.metadata["semantic_seed"] = seed.to_dict()
    return chunk


def attach_seeds_batch(
    chunks: list[TextChunk],
    embedding_seed: EmbeddingResource,
    *,
    domain: str,
) -> list[TextChunk]:
    """Batch-embed every chunk's text and attach a seed to each.

    Single embedder call covers all chunks — the right path for asset
    bodies that emit a list of chunks at once (the common case).
    Returns the same list (chunks mutated in place).
    """
    if not chunks:
        return chunks
    vectors = embedding_seed.embed([c.text for c in chunks])
    for chunk, vector in zip(chunks, vectors, strict=True):
        seed = build_seed(
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            domain=domain,
            embedding=vector,
            embedding_model=embedding_seed.model,
        )
        chunk.metadata["semantic_seed"] = seed.to_dict()
    return chunks


def get_seed(chunk: TextChunk) -> SemanticChunkingSeed | None:
    """Read a previously-attached seed off a chunk, returning ``None``
    when the chunk pre-dates the seed wiring or was emitted by a
    producer that hasn't been upgraded yet."""
    payload = chunk.metadata.get("semantic_seed")
    if not isinstance(payload, dict):
        return None
    try:
        return SemanticChunkingSeed.from_dict(payload)
    except (TypeError, KeyError):
        return None
