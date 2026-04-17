"""Sensors that bridge HEAD manifests to partitioned TAIL pipelines.

Pattern mirrors media_ingest/sensors.py:
  manifest in S3 → diff against registered partitions → register NEW + kick RunRequests

Key difference from media_ingest: bills can be UPDATED (not just new).
The sensor maintains a cursor mapping partition_key → update_date_seen to
detect changes and re-materialize the full tail chain.

No GC on absence — bills don't disappear from the API like media files can
be deleted from NFS.
"""

import json
import os

from dagster import (
    AssetKey,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    sensor,
)

from dagster_io.logging import get_logger
from dagster_io.metrics import DAGSTER_SENSOR_TICK_TOTAL
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)

_MAX_BILL_RUNS_PER_TICK = 50
_MAX_MEMBER_RUNS_PER_TICK = 25

# ── S3 helpers ───────────────────────────────────────────────────────────────


def _get_s3_client() -> S3Client:
    return S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://minio.minio.svc.cluster.local"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )


def _load_manifest(client: S3Client, key: str) -> list[dict]:
    try:
        payload = client.get_object(key)
        return [json.loads(line) for line in payload.decode("utf-8").strip().split("\n") if line.strip()]
    except Exception:
        return []


def _load_cursor(client: S3Client, key: str) -> dict[str, str]:
    try:
        payload = client.get_object(key)
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}


def _save_cursor(client: S3Client, key: str, cursor: dict[str, str]) -> None:
    client.put_object(key, json.dumps(cursor, indent=2).encode("utf-8"))


# ══════════════════════════════════════════════════════════════════════════════
# BILL SENSOR
# ══════════════════════════════════════════════════════════════════════════════


@sensor(
    name="congress_bill_sensor",
    description=(
        "Watches bills_manifest.jsonl for new/updated bill partition keys. "
        "Registers dynamic partitions and triggers the full partitioned tail pipeline."
    ),
    minimum_interval_seconds=900,
    asset_selection=[
        AssetKey("bill_detail"),
        AssetKey("bill_actions"),
        AssetKey("bill_cosponsors"),
        AssetKey("bill_text_versions"),
        AssetKey("bill_amendments"),
        AssetKey("bill_document"),
        AssetKey("bill_chunks"),
        AssetKey("bill_mentions"),
        AssetKey("bill_assertions"),
        AssetKey("bill_embeddings"),
    ],
)
def congress_bill_sensor(context: SensorEvaluationContext):
    """Detect new/updated bills and kick off per-bill processing."""
    client = _get_s3_client()
    manifest = _load_manifest(client, "silver/congress_data/manifests/bills_manifest.jsonl")

    if not manifest:
        DAGSTER_SENSOR_TICK_TOTAL.labels(
            code_location="congress_data", sensor_name="congress_bill_sensor", outcome="skipped"
        ).inc()
        yield SkipReason("No bills manifest found in S3")
        return

    # Load cursor (partition_key → update_date last seen)
    cursor_key = "silver/congress_data/state/bill_sensor_cursor.json"
    cursor = _load_cursor(client, cursor_key)

    # Get registered partitions
    existing_partitions = set(context.instance.get_dynamic_partitions("congress_bill"))

    # Classify each manifest entry
    new_keys: list[str] = []
    updated_keys: list[str] = []

    for entry in manifest:
        pk = entry["partition_key"]
        update_date = entry.get("update_date", "")

        if pk not in existing_partitions:
            new_keys.append(pk)
        elif update_date and update_date > cursor.get(pk, ""):
            updated_keys.append(pk)

    actionable = new_keys + updated_keys
    if not actionable:
        DAGSTER_SENSOR_TICK_TOTAL.labels(
            code_location="congress_data", sensor_name="congress_bill_sensor", outcome="skipped"
        ).inc()
        yield SkipReason(f"All {len(manifest)} bills up to date ({len(existing_partitions)} registered)")
        return

    # Cap batch size
    batch = actionable[:_MAX_BILL_RUNS_PER_TICK]
    batch_new = [k for k in batch if k in set(new_keys)]
    batch_updated = [k for k in batch if k not in set(new_keys)]

    # Register new partition keys
    if batch_new:
        context.instance.add_dynamic_partitions("congress_bill", batch_new)
        logger.info("congress_bill_sensor: registered %d new partitions", len(batch_new))

    # Yield RunRequests
    manifest_by_key = {e["partition_key"]: e for e in manifest}
    for pk in batch:
        context.log.info(f"Bill {'NEW' if pk in set(new_keys) else 'UPDATED'}: {pk}")
        yield RunRequest(
            run_key=f"congress_bill_{pk}_{manifest_by_key[pk].get('update_date', '')}",
            partition_key=pk,
        )
        # Update cursor
        cursor[pk] = manifest_by_key[pk].get("update_date", "")

    # Save cursor
    _save_cursor(client, cursor_key, cursor)

    DAGSTER_SENSOR_TICK_TOTAL.labels(
        code_location="congress_data", sensor_name="congress_bill_sensor", outcome="success"
    ).inc()
    logger.info(
        "congress_bill_sensor: yielded %d runs (%d new, %d updated, %d pending)",
        len(batch),
        len(batch_new),
        len(batch_updated),
        len(actionable) - len(batch),
    )


# ══════════════════════════════════════════════════════════════════════════════
# MEMBER SENSOR
# ══════════════════════════════════════════════════════════════════════════════


@sensor(
    name="congress_member_sensor",
    description=(
        "Watches members_manifest.jsonl for new/updated member partition keys. "
        "Registers dynamic partitions and triggers the member tail pipeline."
    ),
    minimum_interval_seconds=900,
    asset_selection=[
        AssetKey("member_detail"),
        AssetKey("member_committee_assignments"),
        AssetKey("member_sponsored"),
        AssetKey("member_cosponsored"),
        AssetKey("member_document"),
        AssetKey("member_chunks"),
        AssetKey("member_mentions"),
        AssetKey("member_embeddings"),
    ],
)
def congress_member_sensor(context: SensorEvaluationContext):
    """Detect new/updated members and kick off per-member processing."""
    client = _get_s3_client()
    manifest = _load_manifest(client, "silver/congress_data/manifests/members_manifest.jsonl")

    if not manifest:
        DAGSTER_SENSOR_TICK_TOTAL.labels(
            code_location="congress_data", sensor_name="congress_member_sensor", outcome="skipped"
        ).inc()
        yield SkipReason("No members manifest found in S3")
        return

    cursor_key = "silver/congress_data/state/member_sensor_cursor.json"
    cursor = _load_cursor(client, cursor_key)
    existing_partitions = set(context.instance.get_dynamic_partitions("congress_member"))

    new_keys: list[str] = []
    updated_keys: list[str] = []

    for entry in manifest:
        pk = entry["partition_key"]
        update_date = entry.get("update_date", "")

        if pk not in existing_partitions:
            new_keys.append(pk)
        elif update_date and update_date > cursor.get(pk, ""):
            updated_keys.append(pk)

    actionable = new_keys + updated_keys
    if not actionable:
        DAGSTER_SENSOR_TICK_TOTAL.labels(
            code_location="congress_data", sensor_name="congress_member_sensor", outcome="skipped"
        ).inc()
        yield SkipReason(f"All {len(manifest)} members up to date")
        return

    batch = actionable[:_MAX_MEMBER_RUNS_PER_TICK]
    batch_new = [k for k in batch if k in set(new_keys)]

    if batch_new:
        context.instance.add_dynamic_partitions("congress_member", batch_new)

    manifest_by_key = {e["partition_key"]: e for e in manifest}
    for pk in batch:
        context.log.info(f"Member {'NEW' if pk in set(new_keys) else 'UPDATED'}: {pk}")
        yield RunRequest(
            run_key=f"congress_member_{pk}_{manifest_by_key[pk].get('update_date', '')}",
            partition_key=pk,
        )
        cursor[pk] = manifest_by_key[pk].get("update_date", "")

    _save_cursor(client, cursor_key, cursor)

    DAGSTER_SENSOR_TICK_TOTAL.labels(
        code_location="congress_data", sensor_name="congress_member_sensor", outcome="success"
    ).inc()
    logger.info(
        "congress_member_sensor: yielded %d runs (%d new, %d updated)",
        len(batch),
        len(batch_new),
        len(batch) - len(batch_new),
    )
