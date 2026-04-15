"""Gold: Speaker-aware text chunking for embedding + extraction.

Uses merged speaker turns as natural chunk boundaries. Each turn becomes
a chunk if it's under the size limit. Oversized turns (long monologues)
are split at natural speech pauses detected from word-level timestamps.

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
# Minimum pause between words (seconds) to consider as a split point
PAUSE_THRESHOLD_S = 1.0

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


def _split_on_pauses(words: list[dict], text: str, pause_threshold: float = PAUSE_THRESHOLD_S) -> list[dict]:
    """Split a word list at natural speech pauses.

    Returns a list of sub-segments, each with text, start_s, end_s, and words.
    If no pauses found, returns the whole thing as a single segment.
    """
    if not words:
        return [{"text": text, "start": 0, "end": 0, "words": []}]

    # Find pause points — word boundaries where the gap exceeds threshold
    split_indices = []
    for i in range(1, len(words)):
        gap = words[i].get("start", 0) - words[i - 1].get("end", 0)
        if gap >= pause_threshold:
            split_indices.append(i)

    if not split_indices:
        # No pauses found — return as single segment
        return [
            {
                "text": text,
                "start": words[0].get("start", 0),
                "end": words[-1].get("end", 0),
                "words": words,
            }
        ]

    # Split at pause points
    sub_segments = []
    prev = 0
    for split_at in split_indices:
        chunk_words = words[prev:split_at]
        if chunk_words:
            chunk_text = "".join(w.get("word", "") for w in chunk_words).strip()
            sub_segments.append(
                {
                    "text": chunk_text,
                    "start": chunk_words[0].get("start", 0),
                    "end": chunk_words[-1].get("end", 0),
                    "words": chunk_words,
                }
            )
        prev = split_at

    # Last segment
    chunk_words = words[prev:]
    if chunk_words:
        chunk_text = "".join(w.get("word", "") for w in chunk_words).strip()
        sub_segments.append(
            {
                "text": chunk_text,
                "start": chunk_words[0].get("start", 0),
                "end": chunk_words[-1].get("end", 0),
                "words": chunk_words,
            }
        )

    return sub_segments


def _speaker_turn_chunks(
    segments: list[dict],
    document_id: str,
    title: str,
    chunking: ChunkingResource,
    metadata: dict,
) -> list[TextChunk]:
    """Build chunks from speaker turns, splitting oversized turns at speech pauses."""
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
            # Oversized turn — split at natural speech pauses
            sub_segs = _split_on_pauses(words, text)

            for sub in sub_segs:
                sub_text = sub["text"]
                if not sub_text:
                    continue

                if len(sub_text) <= MAX_CHUNK_CHARS:
                    # Pause-split chunk fits — use as-is
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
                                "start_s": sub["start"],
                                "end_s": sub["end"],
                                "strategy": "speech_pause_split",
                            },
                        )
                    )
                    chunk_index += 1
                else:
                    # Still too big after pause split (or no pauses found) —
                    # fall back to text splitter with proportional timestamps
                    fallback_texts = chunking.split_text(sub_text, chunk_size=800, chunk_overlap=0)
                    n = len(fallback_texts)
                    sub_dur = sub["end"] - sub["start"]
                    for i_fb, fb_text in enumerate(fallback_texts):
                        fb_start = sub["start"] + sub_dur * (i_fb / n)
                        fb_end = sub["start"] + sub_dur * ((i_fb + 1) / n)
                        full_text = f"{title}\n\n{fb_text}" if title else fb_text
                        chunks.append(
                            TextChunk(
                                chunk_id=f"{document_id}:chunk-{chunk_index}",
                                document_id=document_id,
                                text=full_text,
                                index=chunk_index,
                                total_chunks=0,
                                metadata={
                                    **base_meta,
                                    "start_s": fb_start,
                                    "end_s": fb_end,
                                    "strategy": "text_split_fallback",
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
        "media_chunks",
        tracer,
        {"code_location": "media_ingest", "layer": "gold", "partition_key": partition_key},
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
                document_id=doc_id, title=title, content=text, chunk_size=800, chunk_overlap=0
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
        pause_count = len([c for c in chunks if c.metadata.get("strategy") == "speech_pause_split"])
        avg_len = sum(len(c.text) for c in chunks) / max(len(chunks), 1)

        context.log.info(
            f"Chunked '{title}': {len(segments)} turns → {len(chunks)} chunks "
            f"({turn_count} whole turns, {pause_count} pause-split, avg={avg_len:.0f} chars)"
        )

        return Output(
            chunks,
            metadata={
                "document_id": doc_id,
                "title": title,
                "chunk_count": len(chunks),
                "whole_turns": turn_count,
                "pause_split": pause_count,
                "input_segments": len(segments),
            },
        )
