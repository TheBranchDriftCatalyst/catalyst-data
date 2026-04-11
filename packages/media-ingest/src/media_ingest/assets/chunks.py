"""Gold: Text chunking for downstream embedding stage.

Uses 800/150 chunk sizes optimized for speech transcriptions — shorter chunks
improve retrieval quality for conversational audio content.

Partitioned by document_id — each run chunks a single transcription.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io import ChunkingResource, TextChunk
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Speech transcriptions benefit from smaller chunks since spoken language
# is less information-dense than written text.
TRANSCRIPTION_CHUNK_SIZE = 800
TRANSCRIPTION_CHUNK_OVERLAP = 150

# CPU-only text chunking. Small footprint but we set explicit requests/limits
# so the scheduler doesn't co-locate too many of these on a single node.
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


@asset(
    group_name="media_ingest",
    description="Chunk a single media transcription for embedding. One partition = one document.",
    compute_kind="python",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=CHUNKS_K8S_CONFIG,
)
def media_chunks(
    context: AssetExecutionContext,
    chunking: ChunkingResource,
    media_diarization: dict[str, Any],
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
        t = media_diarization
        logger.info("Starting media_chunks chunking for partition=%s", partition_key)

        # Prefer speaker-attributed text for richer chunks
        text = t.get("speaker_text") or t.get("text", "")
        if not text:
            context.log.info(f"No text for partition={partition_key} — returning empty chunks")
            return Output(
                [],
                metadata={
                    "document_id": t.get("document_id", partition_key),
                    "chunk_count": 0,
                    "skipped": True,
                    "reason": "empty_text",
                },
            )

        chunks = chunking.chunk_document(
            document_id=t["document_id"],
            title=t.get("title", ""),
            content=text,
            metadata={
                "source": "media_ingest",
                "language": t.get("language", "unknown"),
                "speaker_count": t.get("speaker_count", 0),
                "speakers": t.get("speakers", []),
            },
            chunk_size=TRANSCRIPTION_CHUNK_SIZE,
            chunk_overlap=TRANSCRIPTION_CHUNK_OVERLAP,
        )

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_chunks", layer="gold").inc(
            len(chunks)
        )
        logger.info(
            "media_chunks complete for partition=%s: %d chunks",
            partition_key,
            len(chunks),
        )
        context.log.info(
            f"Chunked transcription for '{t.get('title', partition_key)}' into {len(chunks)} chunks "
            f"(size={TRANSCRIPTION_CHUNK_SIZE}, overlap={TRANSCRIPTION_CHUNK_OVERLAP})"
        )
        return Output(
            chunks,
            metadata={
                "document_id": t.get("document_id", partition_key),
                "title": t.get("title", ""),
                "chunk_count": len(chunks),
                "chunk_size": TRANSCRIPTION_CHUNK_SIZE,
                "chunk_overlap": TRANSCRIPTION_CHUNK_OVERLAP,
            },
        )
