"""Persist artifacts — cold-path audit event emission for gold-layer outputs.

Emits a ``persist_artifacts`` bench event after mentions and assertions are
materialized to S3. The warm-cache replay path handles its own emit in
tests/benchmark_harness.py; this asset closes the gap for fresh (cold-path)
Dagster runs.

Partitioned by document_id — mirrors media_mentions/media_assertions
cardinality so the persist event lands alongside its source assets in
the State Inspector's downstream panel.
"""

import logging

from dagster import AssetExecutionContext, AssetIn, Output, asset

from dagster_io import Assertion, Mention
from media_ingest.partitions import media_partitions

logger = logging.getLogger(__name__)


@asset(
    group_name="media_ingest",
    description="Emit persist_artifacts audit event after mentions + assertions materialize (cold-path)",
    compute_kind="python",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    ins={
        "media_mentions": AssetIn(input_manager_key="optional_io_manager"),
        "media_assertions": AssetIn(input_manager_key="optional_io_manager"),
    },
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "100m", "memory": "512Mi"},
                    "limits": {"cpu": "500m", "memory": "1Gi"},
                }
            }
        }
    },
)
def mention_proposition_artifacts(
    context: AssetExecutionContext,
    media_mentions: list[Mention] | None = None,
    media_assertions: list[Assertion] | None = None,
) -> Output[dict]:
    """Emit persist_artifacts event for cold-path asset materializations.

    This asset reads the mentions and assertions that were just written to S3
    and emits a synthetic ``persist_artifacts`` audit event with the same shape
    the warm-cache replay path produces. The event includes row counts, S3 keys,
    and dagster_run_id so the State Inspector can render the downstream lineage
    panel.

    Returns a dict with event details for test assertions.
    """
    from catalyst_exgraph.nodes.persist import emit_persist_artifacts

    partition_key = context.partition_key
    mentions = media_mentions or []
    assertions = media_assertions or []

    n_mentions = len(mentions)
    n_assertions = len(assertions)

    logger.info(
        "Emitting persist_artifacts for partition=%s: mentions=%d, assertions=%d",
        partition_key,
        n_mentions,
        n_assertions,
    )
    context.log.info(f"Emitting persist_artifacts: {n_mentions} mentions, {n_assertions} assertions")

    # Build the output_paths map with S3 keys. These are canonical medallion paths
    # where the IO manager wrote the assets.
    output_paths = {
        "media_ingest/mention_artifacts": (
            f"s3://dagster/gold/media_ingest/media/media_mentions/{partition_key}/data.parquet"
        ),
    }
    if n_assertions:
        output_paths["media_ingest/proposition_artifacts"] = (
            f"s3://dagster/gold/media_ingest/media/media_assertions/{partition_key}/data.parquet"
        )

    # Row counts per output — matches the warm-replay schema so the State
    # Inspector's per-asset badges and aggregate counts are consistent.
    row_counts = {
        "media_ingest/mention_artifacts": n_mentions,
    }
    if n_assertions:
        row_counts["media_ingest/proposition_artifacts"] = n_assertions

    try:
        details = emit_persist_artifacts(
            doc_id=partition_key,
            mentions_written=n_mentions,
            propositions_written=n_assertions,
            row_counts=row_counts,
            output_paths=output_paths,
            dagster_run_id=context.run_id,
            source="exgraph",
            extra={"from_cache": False},
        )
    except Exception as e:
        # Fail loud: the event emission is part of the contract, not optional.
        # If it raises, the op should fail so the operator knows something is
        # broken in the audit pipeline.
        logger.exception(
            "persist_artifacts emit failed for partition=%s: %s",
            partition_key,
            e,
        )
        raise

    if details is None:
        logger.warning("persist_artifacts emit returned None (event_store not configured)")
        details = {}

    return Output(
        details,
        metadata={
            "mentions_written": n_mentions,
            "propositions_written": n_assertions,
            "persist_artifacts_emitted": True,
            "document_id": partition_key,
        },
    )
