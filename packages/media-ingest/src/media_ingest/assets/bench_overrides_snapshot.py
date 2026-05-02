"""Export the HITL ``viewer_entity_overrides`` Postgres table to S3.

The training-dataset asset (Phase 3) wants a deterministic snapshot of the
HITL overrides at materialization time so the SFT/DPO JSONL it emits is
reproducible — it can't read live from Postgres without coupling its hash
to wallclock state. This asset is the snapshot boundary: on each
materialization it dumps every active override row to
``s3://<bucket>/bench/overrides/snapshot.json`` and the training asset
reads from there.
"""

import json
from datetime import UTC, datetime
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.bench_store import S3BenchmarkStore
from dagster_io.logging import get_logger

logger = get_logger(__name__)


@asset(
    name="bench_overrides_snapshot",
    group_name="media",
    description=(
        "Snapshot of viewer_entity_overrides → s3://<bucket>/bench/overrides/snapshot.json. "
        "Read by training-dataset assets so SFT/DPO output is deterministic against a "
        "frozen point-in-time view of HITL overrides."
    ),
    compute_kind="postgres",
)
def bench_overrides_snapshot(context: AssetExecutionContext) -> Output[dict[str, Any]]:
    """Dump active viewer_entity_overrides rows to S3 as JSON.

    Imports psycopg + the GraphDBResource configuration lazily so this asset
    can be defined in a code-location that doesn't have psycopg as a hard
    dependency at import time.
    """
    import os

    import psycopg

    pg_host = os.environ.get("KG_PG_HOST", "postgres-knowledge.catalyst-data.svc.cluster.local")
    pg_port = int(os.environ.get("KG_PG_PORT", "5432"))
    pg_database = os.environ.get("KG_PG_DATABASE", "knowledge_graph")
    pg_user = os.environ.get("KG_PG_USER", "kg")
    pg_password = os.environ.get("KG_PG_PASSWORD", "kg-homelab")

    rows: list[dict[str, Any]] = []
    with psycopg.connect(host=pg_host, port=pg_port, dbname=pg_database, user=pg_user, password=pg_password) as conn:
        cur = conn.execute(
            """
            SELECT override_id, alias_text, target_name, entity_type,
                   reviewer, notes, is_active, created_at, updated_at
            FROM viewer_entity_overrides
            WHERE is_active = true
            ORDER BY created_at
            """
        )
        for r in cur.fetchall():
            rows.append(
                {
                    "override_id": r[0],
                    "alias_text": r[1],
                    "target_name": r[2],
                    "entity_type": r[3],
                    "reviewer": r[4] or "",
                    "notes": r[5] or "",
                    "is_active": bool(r[6]),
                    "created_at": r[7].isoformat() if r[7] else None,
                    "updated_at": r[8].isoformat() if r[8] else None,
                }
            )

    snapshot = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "overrides": rows,
    }

    store = S3BenchmarkStore()
    key = f"{store.overrides_prefix}/snapshot.json"
    store.client.put_object(key, json.dumps(snapshot, indent=2, default=str).encode("utf-8"))

    s3_uri = f"s3://{store.bucket}/{key}"
    context.log.info("Wrote %d override rows to %s", len(rows), s3_uri)

    return Output(
        snapshot,
        metadata={
            "row_count": len(rows),
            "s3_uri": MetadataValue.path(s3_uri),
            "generated_at": snapshot["generated_at"],
        },
    )
