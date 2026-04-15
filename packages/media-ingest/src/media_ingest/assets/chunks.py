"""Gold: Speaker-aware text chunking for embedding + extraction.

Splits transcription into chunks that respect natural speech boundaries.
See CHUNKING.md for the full strategy overview.

Hierarchy:
  1. speaker_turn       — whole turn fits (< MAX_CHUNK_CHARS)
  2. speech_pause_split — split at >= PAUSE_THRESHOLD_S word gaps
  3. text_split_fallback — last resort RecursiveCharacterTextSplitter
"""

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io import ChunkingResource, TextChunk
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

MAX_CHUNK_CHARS = 1500
PAUSE_THRESHOLD_S = 1.0
FALLBACK_CHUNK_SIZE = 800

CHUNKS_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "resources": {"requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"cpu": "1", "memory": "2Gi"}}
        }
    }
}


# ── Data types ───────────────────────────────────────────────────────────


@dataclass
class SubSegment:
    """A slice of a speaker turn with text and precise timestamps."""

    text: str
    start: float
    end: float
    strategy: str


# ── Core splitting logic ─────────────────────────────────────────────────


def _sub_segment_from_words(words: list[dict]) -> SubSegment:
    """Build a SubSegment from a contiguous word slice."""
    return SubSegment(
        text="".join(w.get("word", "") for w in words).strip(),
        start=words[0].get("start", 0),
        end=words[-1].get("end", 0),
        strategy="speech_pause_split",
    )


def _split_on_pauses(words: list[dict], text: str, threshold: float = PAUSE_THRESHOLD_S) -> list[SubSegment]:
    """Split a word sequence at natural speech pauses (gaps >= threshold).

    Returns SubSegments with exact word-level timestamps.
    Falls back to the full text as a single segment when no qualifying pauses exist.
    """
    if not words:
        return [SubSegment(text=text, start=0, end=0, strategy="speech_pause_split")]

    # Find indices where the inter-word gap meets the pause threshold
    split_at = [i for i in range(1, len(words)) if words[i].get("start", 0) - words[i - 1].get("end", 0) >= threshold]

    if not split_at:
        return [_sub_segment_from_words(words)]

    # Slice the word list at each pause point
    boundaries = [0, *split_at, len(words)]
    return [
        _sub_segment_from_words(words[boundaries[i] : boundaries[i + 1]])
        for i in range(len(boundaries) - 1)
        if words[boundaries[i] : boundaries[i + 1]]
    ]


def _text_split_fallback(text: str, start: float, end: float, chunking: ChunkingResource) -> list[SubSegment]:
    """Last-resort text splitter with proportional timestamp estimation."""
    pieces = chunking.split_text(text, chunk_size=FALLBACK_CHUNK_SIZE, chunk_overlap=0)
    n = len(pieces)
    duration = end - start
    return [
        SubSegment(
            text=piece,
            start=start + duration * (i / n),
            end=start + duration * ((i + 1) / n),
            strategy="text_split_fallback",
        )
        for i, piece in enumerate(pieces)
    ]


def _segment_to_sub_segments(
    text: str,
    start: float,
    end: float,
    words: list[dict],
    chunking: ChunkingResource,
) -> list[SubSegment]:
    """Convert a single speaker turn into one or more SubSegments.

    Applies the three-tier strategy:
      1. Fits in MAX_CHUNK_CHARS → single SubSegment
      2. Split at speech pauses → multiple SubSegments
      3. Still oversized → text splitter fallback per sub-segment
    """
    if len(text) <= MAX_CHUNK_CHARS:
        return [SubSegment(text=text, start=start, end=end, strategy="speaker_turn")]

    result: list[SubSegment] = []
    for sub in _split_on_pauses(words, text):
        if not sub.text:
            continue
        if len(sub.text) <= MAX_CHUNK_CHARS:
            result.append(sub)
        else:
            result.extend(_text_split_fallback(sub.text, sub.start, sub.end, chunking))
    return result


# ── Chunk builder ────────────────────────────────────────────────────────


def _speaker_turn_chunks(
    segments: list[dict],
    document_id: str,
    title: str,
    chunking: ChunkingResource,
    metadata: dict,
) -> list[TextChunk]:
    """Build TextChunks from speaker-attributed segments."""
    chunks: list[TextChunk] = []

    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue

        speaker = seg.get("speaker", "UNKNOWN")
        sub_segs = _segment_to_sub_segments(
            text=text,
            start=seg.get("start", 0),
            end=seg.get("end", 0),
            words=seg.get("words", []),
            chunking=chunking,
        )

        for sub in sub_segs:
            full_text = f"{title}\n\n{sub.text}" if title else sub.text
            chunks.append(
                TextChunk(
                    chunk_id=f"{document_id}:chunk-{len(chunks)}",
                    document_id=document_id,
                    text=full_text,
                    index=len(chunks),
                    total_chunks=0,
                    metadata={
                        **metadata,
                        "speaker": speaker,
                        "start_s": sub.start,
                        "end_s": sub.end,
                        "strategy": sub.strategy,
                    },
                )
            )

    for c in chunks:
        c.total_chunks = len(chunks)
        c.content_hash = hashlib.sha256(c.text.encode()).hexdigest()

    return chunks


# ── Dagster asset ────────────────────────────────────────────────────────


@asset(
    group_name="media_ingest",
    description="Speaker-aware chunking — preserves turn boundaries, splits monologues at speech pauses.",
    compute_kind="python",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=CHUNKS_K8S_CONFIG,
)
def media_chunks(
    context: AssetExecutionContext,
    chunking: ChunkingResource,
    media_segment_merge: dict[str, Any],
) -> Output[list[TextChunk]]:
    partition_key = context.partition_key
    with trace_operation(
        "media_chunks", tracer, {"code_location": "media_ingest", "layer": "gold", "partition_key": partition_key}
    ):
        t = media_segment_merge
        segments = t.get("segments", [])
        doc_id = t.get("document_id", partition_key)
        title = t.get("title", "")

        if not segments:
            text = t.get("text", "")
            if not text:
                context.log.info(f"No segments or text for partition={partition_key}")
                return Output([], metadata={"document_id": doc_id, "chunk_count": 0, "skipped": True})
            chunks = chunking.chunk_document(doc_id, title, text, chunk_size=FALLBACK_CHUNK_SIZE, chunk_overlap=0)
        else:
            chunks = _speaker_turn_chunks(
                segments,
                doc_id,
                title,
                chunking,
                {
                    "source": "media_ingest",
                    "language": t.get("language", "unknown"),
                    "speaker_count": t.get("speaker_count", 0),
                },
            )

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_chunks", layer="gold").inc(
            len(chunks)
        )

        strategies = Counter(c.metadata.get("strategy") for c in chunks)
        avg_len = sum(len(c.text) for c in chunks) / max(len(chunks), 1)
        context.log.info(
            f"Chunked '{title}': {len(segments)} turns → {len(chunks)} chunks "
            f"(strategies={dict(strategies)}, avg={avg_len:.0f} chars)"
        )

        return Output(
            chunks,
            metadata={
                "document_id": doc_id,
                "chunk_count": len(chunks),
                "input_segments": len(segments),
                **{f"strategy_{k}": v for k, v in strategies.items()},
            },
        )
