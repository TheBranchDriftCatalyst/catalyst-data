"""Gold: Speaker-aware text chunking for embedding + extraction.

Uses merged speaker turns as natural chunk boundaries. Each turn becomes
a chunk if it's under the size limit. Oversized turns (long monologues)
are split with RecursiveCharacterTextSplitter as fallback.

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

MAX_CHUNK_CHARS = 1500
SPLIT_CHUNK_SIZE = 800
SPLIT_CHUNK_OVERLAP = 0

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


def _word_timestamps_for_char_range(
    words: list[dict], char_start: int, char_end: int, fallback_start: float, fallback_end: float
) -> tuple[float, float]:
    """Map a character range in the turn text to word-level timestamps.

    Walks the word list accumulating character positions. Words whose
    position falls within [char_start, char_end) contribute their timestamps.
    """
    if not words:
        return fallback_start, fallback_end

    first_ts = None
    last_ts = None
    pos = 0

    for w in words:
        wlen = len(w.get("word", ""))
        wend = pos + wlen
        if wend > char_start and pos < char_end:
            if first_ts is None:
                first_ts = w.get("start", fallback_start)
            last_ts = w.get("end", fallback_end)
        pos = wend

    return (first_ts or fallback_start, last_ts or fallback_end)


def _speaker_turn_chunks(
    segments: list[dict],
    document_id: str,
    title: str,
    chunking: ChunkingResource,
    metadata: dict,
) -> list[TextChunk]:
    """Build chunks from speaker turns, splitting oversized turns."""
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

        base_meta = {**metadata, "speaker": speaker}

        if len(text) <= MAX_CHUNK_CHARS:
            full_text = f"{title}\n\n{text}" if title else text
            chunks.append(
                TextChunk(
                    chunk_id=f"{document_id}:chunk-{chunk_index}",
                    document_id=document_id,
                    text=full_text,
                    index=chunk_index,
                    total_chunks=0,
                    metadata={**base_meta, "start_s": start_s, "end_s": end_s, "strategy": "speaker_turn"},
                )
            )
            chunk_index += 1
        else:
            # Split the raw text (no prefix).
            sub_texts = chunking.split_text(text, chunk_size=SPLIT_CHUNK_SIZE, chunk_overlap=SPLIT_CHUNK_OVERLAP)
            n = len(sub_texts)

            # Proportional timestamp assignment: divide the turn's time range
            # evenly across sub-chunks, then refine with word-level data.
            turn_duration = end_s - start_s
            for i_sub, sub_text in enumerate(sub_texts):
                # Proportional time range for this sub-chunk
                frac_start = i_sub / n
                frac_end = (i_sub + 1) / n
                prop_start = start_s + turn_duration * frac_start
                prop_end = start_s + turn_duration * frac_end

                # Refine with word-level data if available
                text_len = len(text) if len(text) > 0 else 1
                char_start = int(text_len * frac_start)
                char_end = int(text_len * frac_end)
                sub_start, sub_end = _word_timestamps_for_char_range(words, char_start, char_end, prop_start, prop_end)

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
            text = t.get("text", "")
            if not text:
                context.log.info(f"No segments or text for partition={partition_key}")
                return Output([], metadata={"document_id": doc_id, "chunk_count": 0, "skipped": True})
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
