"""Gold: Speaker-aware text chunking for embedding + extraction.

Uses merged speaker turns as natural chunk boundaries. Each turn becomes
a chunk if it's under the size limit. Oversized turns (long monologues)
are split with RecursiveCharacterTextSplitter as fallback.

This preserves semantic coherence — each chunk is one person saying one
thing, not an arbitrary 800-char window that breaks mid-sentence.

Partitioned by document_id — each run chunks a single transcription.
"""

import hashlib
from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io import ChunkingResource, TextChunk
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Max chars per chunk. Speaker turns under this limit are kept whole.
# Turns over this get split by RecursiveCharacterTextSplitter.
MAX_CHUNK_CHARS = 1500
SPLIT_CHUNK_SIZE = 800
SPLIT_CHUNK_OVERLAP = 150

CHUNKS_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "resources": {
                "requests": {"cpu": "250m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "2Gi"},
            },
        },
    },
}


def _resolve_sub_chunk_timestamps(
    sub_text: str, words: list[dict], seg_start: float, seg_end: float
) -> tuple[float, float]:
    """Find the precise start/end timestamps for a sub-chunk using word-level data.

    Walks the word list and finds which words overlap with the sub-chunk text.
    Returns (start_s, end_s) for the sub-chunk. Falls back to segment
    boundaries if word matching fails.
    """
    if not words:
        return seg_start, seg_end

    # Strip speaker prefix for matching
    clean = sub_text
    if clean.startswith("["):
        bracket_end = clean.find("] ")
        if bracket_end > 0:
            clean = clean[bracket_end + 2 :]

    # Find first and last word that appears in the sub-chunk text
    first_ts = None
    last_ts = None
    search_pos = 0

    for w in words:
        word_text = w.get("word", "").strip()
        if not word_text:
            continue
        idx = clean.find(word_text, search_pos)
        if idx >= 0:
            if first_ts is None:
                first_ts = w.get("start", seg_start)
            last_ts = w.get("end", seg_end)
            search_pos = idx + len(word_text)

    return (first_ts or seg_start, last_ts or seg_end)


def _speaker_turn_chunks(
    segments: list[dict],
    document_id: str,
    title: str,
    chunking: ChunkingResource,
    metadata: dict,
) -> list[TextChunk]:
    """Build chunks from speaker turns, splitting oversized turns.

    Each merged segment is a speaker turn. If the turn text is under
    MAX_CHUNK_CHARS, it becomes one chunk with the speaker label preserved.
    If over, it's split with the text splitter and each sub-chunk gets
    word-level timestamp resolution for precise provenance.
    """
    chunks: list[TextChunk] = []
    chunk_index = 0

    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue

        speaker = seg.get("speaker", "UNKNOWN")
        start_s = seg.get("start", 0)
        end_s = seg.get("end", 0)
        words = seg.get("words", [])

        base_meta = {
            **metadata,
            "speaker": speaker,
        }

        if len(text) <= MAX_CHUNK_CHARS:
            # Natural turn fits in one chunk — use as-is
            full_text = f"[{speaker}] {text}" if speaker else text
            if title:
                full_text = f"{title}\n\n{full_text}"
            chunks.append(
                TextChunk(
                    chunk_id=f"{document_id}:chunk-{chunk_index}",
                    document_id=document_id,
                    text=full_text,
                    index=chunk_index,
                    total_chunks=0,  # set after loop
                    metadata={**base_meta, "start_s": start_s, "end_s": end_s, "strategy": "speaker_turn"},
                )
            )
            chunk_index += 1
        else:
            # Oversized turn — split with text splitter, resolve timestamps per sub-chunk
            prefixed = f"[{speaker}] {text}" if speaker else text
            sub_chunks = chunking.split_text(
                prefixed,
                chunk_size=SPLIT_CHUNK_SIZE,
                chunk_overlap=SPLIT_CHUNK_OVERLAP,
            )
            for sub_text in sub_chunks:
                sub_start, sub_end = _resolve_sub_chunk_timestamps(sub_text, words, start_s, end_s)
                full_text = f"{title}\n\n{sub_text}" if title else sub_text
                chunks.append(
                    TextChunk(
                        chunk_id=f"{document_id}:chunk-{chunk_index}",
                        document_id=document_id,
                        text=full_text,
                        index=chunk_index,
                        total_chunks=0,
                        metadata={
                            **base_meta,
                            "start_s": sub_start,
                            "end_s": sub_end,
                            "strategy": "speaker_turn_split",
                        },
                    )
                )
                chunk_index += 1

    # Backfill total_chunks
    for c in chunks:
        c.total_chunks = len(chunks)
        c.content_hash = hashlib.sha256(c.text.encode()).hexdigest()

    return chunks


@asset(
    group_name="media_ingest",
    description="Speaker-aware chunking — preserves turn boundaries, splits only oversized monologues.",
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
        "media_chunks",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
        },
    ):
        t = media_segment_merge
        segments = t.get("segments", [])
        title = t.get("title", "")
        doc_id = t.get("document_id", partition_key)

        if not segments:
            # Fallback: no segments (transcription failed?) — try raw text
            text = t.get("text", "")
            if not text:
                context.log.info(f"No segments or text for partition={partition_key}")
                return Output([], metadata={"document_id": doc_id, "chunk_count": 0, "skipped": True})

            # Use traditional chunking as last resort
            chunks = chunking.chunk_document(
                document_id=doc_id,
                title=title,
                content=text,
                chunk_size=SPLIT_CHUNK_SIZE,
                chunk_overlap=SPLIT_CHUNK_OVERLAP,
            )
        else:
            meta = {
                "source": "media_ingest",
                "language": t.get("language", "unknown"),
                "speaker_count": t.get("speaker_count", 0),
            }
            chunks = _speaker_turn_chunks(segments, doc_id, title, chunking, meta)

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_chunks", layer="gold").inc(
            len(chunks)
        )

        turn_count = len([c for c in chunks if c.metadata.get("strategy") == "speaker_turn"])
        split_count = len([c for c in chunks if c.metadata.get("strategy") == "speaker_turn_split"])
        avg_len = sum(len(c.text) for c in chunks) / max(len(chunks), 1)

        context.log.info(
            f"Chunked '{title}': {len(segments)} turns → {len(chunks)} chunks "
            f"({turn_count} whole turns, {split_count} split sub-chunks, avg={avg_len:.0f} chars)"
        )

        return Output(
            chunks,
            metadata={
                "document_id": doc_id,
                "title": title,
                "chunk_count": len(chunks),
                "whole_turns": turn_count,
                "split_sub_chunks": split_count,
                "input_segments": len(segments),
                "max_chunk_chars": MAX_CHUNK_CHARS,
            },
        )
