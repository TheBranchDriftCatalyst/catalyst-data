"""Gold: Entity candidate resolution via concordance engine.

Groups mentions into EntityCandidates within the media_ingest code location
using multi-pass resolution (exact match, substring, Jaccard, embedding cosine).
"""

from dagster import AssetExecutionContext, Output, asset

import dagster_io.concordance as _concordance_mod
from dagster_io import (
    ConcordanceEngine,
    EmbeddingResource,
    EntityCandidate,
    Mention,
)
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED, ENTITY_REDUCTION_RATIO
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.versioning import code_version_from_modules
from media_ingest.partitions import media_partitions

_CODE_VERSION = code_version_from_modules(_concordance_mod)

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="media_ingest",
    description="Resolve media mentions into entity candidates via concordance engine",
    compute_kind="python",
    code_version=_CODE_VERSION,
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "500m", "memory": "2Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi"},
                }
            }
        }
    },
)
def media_entity_candidates(
    context: AssetExecutionContext,
    embeddings: EmbeddingResource,
    media_mentions: list[Mention],
) -> Output[list[EntityCandidate]]:
    partition_key = context.partition_key
    with trace_operation(
        "media_entity_candidates",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
            "mention_count": len(media_mentions),
        },
    ):
        logger.info(
            "Starting media_entity_candidates resolution for partition=%s (%d mentions)",
            partition_key,
            len(media_mentions),
        )
        context.log.info(f"Resolving {len(media_mentions)} mentions into entity candidates")

        if not media_mentions:
            context.log.info(f"No mentions for partition={partition_key} — returning empty candidates")
            return Output(
                [],
                metadata={
                    "mention_count": 0,
                    "candidate_count": 0,
                    "document_id": partition_key,
                },
            )

        # Collect unique surface forms for embedding
        unique_texts = sorted({m.text.lower().strip() for m in media_mentions})
        context.log.info(f"Embedding {len(unique_texts)} unique surface forms")

        # Embed all unique surface forms
        if unique_texts:
            vectors = embeddings.embed(unique_texts)
            embedding_map = dict(zip(unique_texts, vectors, strict=False))
        else:
            embedding_map = {}

        # Run concordance engine
        engine = ConcordanceEngine()
        candidates = engine.resolve(
            mentions=media_mentions,
            code_location="media_ingest",
            embeddings=embedding_map,
        )

        ASSET_RECORDS_PROCESSED.labels(
            code_location="media_ingest",
            asset_key="media_entity_candidates",
            layer="gold",
        ).inc(len(candidates))

        # Concordance dedup effectiveness — observed as a histogram so Grafana
        # can show distribution / percentiles alongside the existing asset
        # metadata emission (which is lost at the asset boundary).
        reduction_ratio = round(len(candidates) / max(len(unique_texts), 1), 3)
        ENTITY_REDUCTION_RATIO.labels(code_location="media_ingest").observe(reduction_ratio)

        logger.info(
            "media_entity_candidates complete for partition=%s: %d mentions -> %d candidates",
            partition_key,
            len(media_mentions),
            len(candidates),
        )
        context.log.info(f"Resolved {len(media_mentions)} mentions -> {len(candidates)} entity candidates")

        return Output(
            candidates,
            metadata={
                "document_id": partition_key,
                "mention_count": len(media_mentions),
                "candidate_count": len(candidates),
                "unique_surface_forms": len(unique_texts),
                "reduction_ratio": reduction_ratio,
            },
        )
