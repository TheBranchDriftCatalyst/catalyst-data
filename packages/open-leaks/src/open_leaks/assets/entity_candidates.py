"""Gold: Entity candidate resolution via concordance engine.

Groups mentions into EntityCandidates within the open_leaks code location
using multi-pass resolution (exact match, substring, Jaccard, embedding cosine).
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import (
    ConcordanceEngine,
    EmbeddingResource,
    EntityCandidate,
    Mention,
)

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="leaks",
    description="Resolve leak mentions into entity candidates via concordance engine",
    compute_kind="python",
    metadata={"layer": "gold"},
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
def leak_entity_candidates(
    context: AssetExecutionContext,
    embeddings: EmbeddingResource,
    leak_mentions: list[Mention],
) -> Output[list[EntityCandidate]]:
    with trace_operation("leak_entity_candidates", tracer, {"code_location": "open_leaks", "layer": "gold", "mention_count": len(leak_mentions)}):
        logger.info("Starting leak_entity_candidates resolution for %d mentions", len(leak_mentions))
        context.log.info(f"Resolving {len(leak_mentions)} mentions into entity candidates")

        # Collect unique surface forms for embedding
        unique_texts = sorted({m.text.lower().strip() for m in leak_mentions})
        context.log.info(f"Embedding {len(unique_texts)} unique surface forms")

        # Embed all unique surface forms
        if unique_texts:
            vectors = embeddings.embed(unique_texts)
            embedding_map = dict(zip(unique_texts, vectors))
        else:
            embedding_map = {}

        # Run concordance engine
        engine = ConcordanceEngine()
        candidates = engine.resolve(
            mentions=leak_mentions,
            code_location="open_leaks",
            embeddings=embedding_map,
        )

        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="leak_entity_candidates", layer="gold").inc(len(candidates))
        logger.info("leak_entity_candidates complete: %d mentions -> %d candidates", len(leak_mentions), len(candidates))
        context.log.info(
            f"Resolved {len(leak_mentions)} mentions → {len(candidates)} entity candidates"
        )

        return Output(
            candidates,
            metadata={
                "mention_count": len(leak_mentions),
                "candidate_count": len(candidates),
                "unique_surface_forms": len(unique_texts),
                "reduction_ratio": round(len(candidates) / max(len(unique_texts), 1), 3),
            },
        )
