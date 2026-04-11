"""Gold: Vector embeddings for media transcription chunks.

Partitioned by document_id — each run embeds one document's chunks.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io import EmbeddingResource, TextChunk
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="media_ingest",
    description="Generate vector embeddings for one document's transcription chunks",
    compute_kind="ml",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "1", "memory": "4Gi"},
                    "limits": {"cpu": "2", "memory": "8Gi"},
                }
            }
        }
    },
)
def media_embeddings(
    context: AssetExecutionContext,
    embeddings: EmbeddingResource,
    media_chunks: list[TextChunk],
) -> Output[list[dict[str, Any]]]:
    partition_key = context.partition_key
    with trace_operation(
        "media_embeddings",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
            "chunk_count": len(media_chunks),
        },
    ):
        logger.info(
            "Starting media_embeddings for partition=%s (%d chunks)",
            partition_key,
            len(media_chunks),
        )

        if not media_chunks:
            context.log.info(f"No chunks for partition={partition_key} — returning empty embeddings")
            return Output(
                [],
                metadata={
                    "document_id": partition_key,
                    "embedding_count": 0,
                },
            )

        texts = [chunk.text for chunk in media_chunks]

        context.log.info(f"Embedding {len(texts)} chunks with model={embeddings.model}")
        vectors = embeddings.embed(texts)
        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_embeddings", layer="gold").inc(
            len(vectors)
        )
        logger.info(
            "media_embeddings complete for partition=%s: %d vectors (%dd)",
            partition_key,
            len(vectors),
            len(vectors[0]) if vectors else 0,
        )

        results = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "embedding": vec,
                "model": embeddings.model,
                "dimensions": len(vec),
            }
            for chunk, vec in zip(media_chunks, vectors, strict=False)
        ]

        context.log.info(f"Generated {len(results)} embeddings ({len(vectors[0])}d)")
        return Output(
            results,
            metadata={
                "document_id": partition_key,
                "embedding_count": len(results),
                "model": embeddings.model,
                "dimensions": len(vectors[0]) if vectors else 0,
            },
        )
