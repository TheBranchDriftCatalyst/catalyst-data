"""Gold: Speaker-aware text chunking for embedding + extraction.

Thin Dagster asset wrapping ``ChunkingResource.chunk_speaker_segments`` (in
``dagster_io.chunking``). The chunking strategy itself lives in the resource
so the Dagster UI launchpad ``chunk_size`` setting controls audio chunking
the same way it controls text chunking, and so future strategy variants (VAD
windows, sentence-boundary, etc.) can be added as resource methods without
touching this asset.

See CHUNKING.md and ``dagster_io.chunking.ChunkingResource.chunk_speaker_segments``
for the three-tier strategy details (speaker_turn → speech_pause_split →
text_split_fallback).
"""

from collections import Counter
from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io import ChunkingResource, TextChunk
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

CHUNKS_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "resources": {"requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"cpu": "1", "memory": "2Gi"}}
        }
    }
}


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
            chunks = chunking.chunk_document(doc_id, title, text, chunk_overlap=0)
        else:
            chunks = chunking.chunk_speaker_segments(
                segments,
                doc_id,
                title,
                metadata={
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
