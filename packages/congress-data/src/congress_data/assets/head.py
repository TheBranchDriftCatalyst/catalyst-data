"""HEAD assets: unpartitioned incremental list pulls + manifest writes.

These assets form the discovery layer that feeds the sensors.
Pattern mirrors media_ingest: scheduled discovery → manifest → sensor → tail.

bills_list_incremental → bills_manifest → congress_bill_sensor
members_list_incremental → members_manifest → congress_member_sensor
"""

import json
import os
from datetime import datetime

from dagster import AssetExecutionContext, Output, asset

from congress_data.client import CongressAPIClient
from congress_data.config import CongressionalConfig
from congress_data.entities import Bill, Member
from congress_data.partitions import make_bill_partition_key
from congress_data.watermark import WatermarkManager
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)
tracer = get_tracer(__name__)

CONGRESS_API_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "env_from": [
                {"secret_ref": {"name": "congress-data-secrets"}},
            ],
        },
    },
}

MANIFEST_BUCKET = os.environ.get("DAGSTER_S3_BUCKET", "dagster")


def _get_s3_client() -> S3Client:
    return S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://minio.minio.svc.cluster.local"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=MANIFEST_BUCKET,
    )


# ══════════════════════════════════════════════════════════════════════════════
# BILLS HEAD
# ══════════════════════════════════════════════════════════════════════════════


@asset(
    group_name="congress",
    description="Incremental list pull of congressional bills with watermark tracking",
    compute_kind="api_pull",
    metadata={"layer": "bronze"},
    op_tags=CONGRESS_API_K8S_CONFIG,
)
def bills_list_incremental(
    context: AssetExecutionContext,
    config: CongressionalConfig,
) -> Output[list[dict]]:
    """Pull bill list entries updated since last watermark.

    Emits list of {partition_key, update_date, update_date_including_text, api_url}
    """
    with trace_operation(
        "bills_list_incremental",
        tracer,
        {"code_location": "congress_data", "layer": "bronze", "congress": config.congress_number},
    ):
        wm_manager = WatermarkManager()
        watermark = wm_manager.load("bills_watermark.json")

        from_dt = watermark.last_update_date
        context.log.info(f"Bills incremental pull: congress={config.congress_number}, from_dt={from_dt}")

        with CongressAPIClient(api_key=config.congress_api_key) as client:
            entries: list[dict] = []
            max_update_date: datetime | None = None
            max_update_date_text: datetime | None = None

            for bill_data in client.iterate_bills(
                congress=config.congress_number,
                from_dt=from_dt,
                sort="updateDate+asc",
            ):
                bill = Bill.from_api_list_item(bill_data, congress=config.congress_number)
                partition_key = make_bill_partition_key(bill.congress, bill.bill_type, bill.number)

                entry = {
                    "partition_key": partition_key,
                    "bill_id": bill.id,
                    "congress": bill.congress,
                    "bill_type": bill.bill_type,
                    "number": bill.number,
                    "title": bill.title,
                    "update_date": bill.update_date.isoformat() if bill.update_date else None,
                    "update_date_including_text": (
                        bill.update_date_including_text.isoformat() if bill.update_date_including_text else None
                    ),
                    "api_url": bill.api_url,
                }
                entries.append(entry)

                # Track max watermarks
                if bill.update_date and (not max_update_date or bill.update_date > max_update_date):
                    max_update_date = bill.update_date
                if bill.update_date_including_text and (
                    not max_update_date_text or bill.update_date_including_text > max_update_date_text
                ):
                    max_update_date_text = bill.update_date_including_text

        # Save watermark on success
        if entries:
            watermark.last_update_date = max_update_date or watermark.last_update_date
            watermark.last_update_date_including_text = (
                max_update_date_text or watermark.last_update_date_including_text
            )
            wm_manager.save("bills_watermark.json", watermark, run_id=context.run_id)

        ASSET_RECORDS_PROCESSED.labels(
            code_location="congress_data", asset_key="bills_list_incremental", layer="bronze"
        ).inc(len(entries))
        context.log.info(f"Bills incremental: {len(entries)} entries (from_dt={from_dt})")

        return Output(
            entries,
            metadata={
                "count": len(entries),
                "congress": config.congress_number,
                "from_datetime": str(from_dt),
                "max_update_date": str(max_update_date),
            },
        )


@asset(
    group_name="congress",
    description="Merge incremental bill entries into persistent manifest",
    compute_kind="transform",
    metadata={"layer": "silver"},
)
def bills_manifest(
    context: AssetExecutionContext,
    bills_list_incremental: list[dict],
) -> Output[list[dict]]:
    """Merge new entries into bills_manifest.jsonl in S3.

    Returns the full manifest (existing + new, deduped by partition_key).
    """
    with trace_operation("bills_manifest", tracer, {"code_location": "congress_data", "layer": "silver"}):
        manifest_key = "silver/congress_data/manifests/bills_manifest.jsonl"
        client = _get_s3_client()

        # Load existing manifest
        existing: dict[str, dict] = {}
        try:
            payload = client.get_object(manifest_key)
            for line in payload.decode("utf-8").strip().split("\n"):
                if line.strip():
                    entry = json.loads(line)
                    existing[entry["partition_key"]] = entry
        except Exception:
            context.log.info("No existing bills manifest — cold start")

        # Merge: new entries win over existing (they have newer update_date)
        for entry in bills_list_incremental:
            existing[entry["partition_key"]] = entry

        manifest = list(existing.values())

        # Write back
        lines = [json.dumps(e, default=str) for e in manifest]
        client.put_object(manifest_key, ("\n".join(lines) + "\n").encode("utf-8"))

        context.log.info(f"Bills manifest: {len(manifest)} total ({len(bills_list_incremental)} new/updated)")

        return Output(
            manifest,
            metadata={
                "total_bills": len(manifest),
                "new_or_updated": len(bills_list_incremental),
                "manifest_key": manifest_key,
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# MEMBERS HEAD
# ══════════════════════════════════════════════════════════════════════════════


@asset(
    group_name="congress",
    description="Incremental list pull of congressional members",
    compute_kind="api_pull",
    metadata={"layer": "bronze"},
    op_tags=CONGRESS_API_K8S_CONFIG,
)
def members_list_incremental(
    context: AssetExecutionContext,
    config: CongressionalConfig,
) -> Output[list[dict]]:
    """Pull member list entries updated since last watermark.

    NOTE: /member/congress/{c} does NOT support fromDateTime.
    Uses /member?fromDateTime=...&currentMember=True for incremental pulls.
    """
    with trace_operation(
        "members_list_incremental",
        tracer,
        {"code_location": "congress_data", "layer": "bronze", "congress": config.congress_number},
    ):
        wm_manager = WatermarkManager()
        watermark = wm_manager.load("members_watermark.json")
        from_dt = watermark.last_update_date

        context.log.info(f"Members incremental pull: congress={config.congress_number}, from_dt={from_dt}")

        with CongressAPIClient(api_key=config.congress_api_key) as client:
            entries: list[dict] = []
            max_update_date: datetime | None = None

            # Use congress-scoped endpoint for cold start, incremental /member for updates
            iterator = client.iterate_members(
                congress=config.congress_number if not from_dt else None,
                from_dt=from_dt,
                current_member=True if from_dt else None,
            )

            for member_data in iterator:
                member = Member.from_api_list_item(member_data)

                entry = {
                    "partition_key": member.bioguide_id,
                    "bioguide_id": member.bioguide_id,
                    "name": member.name,
                    "update_date": member.update_date.isoformat() if member.update_date else None,
                    "api_url": member.api_url,
                }
                entries.append(entry)

                if member.update_date and (not max_update_date or member.update_date > max_update_date):
                    max_update_date = member.update_date

        # Save watermark on success
        if entries:
            watermark.last_update_date = max_update_date or watermark.last_update_date
            wm_manager.save("members_watermark.json", watermark, run_id=context.run_id)

        ASSET_RECORDS_PROCESSED.labels(
            code_location="congress_data", asset_key="members_list_incremental", layer="bronze"
        ).inc(len(entries))
        context.log.info(f"Members incremental: {len(entries)} entries")

        return Output(
            entries,
            metadata={
                "count": len(entries),
                "congress": config.congress_number,
                "from_datetime": str(from_dt),
            },
        )


@asset(
    group_name="congress",
    description="Merge incremental member entries into persistent manifest",
    compute_kind="transform",
    metadata={"layer": "silver"},
)
def members_manifest(
    context: AssetExecutionContext,
    members_list_incremental: list[dict],
) -> Output[list[dict]]:
    """Merge new entries into members_manifest.jsonl in S3."""
    with trace_operation("members_manifest", tracer, {"code_location": "congress_data", "layer": "silver"}):
        manifest_key = "silver/congress_data/manifests/members_manifest.jsonl"
        client = _get_s3_client()

        existing: dict[str, dict] = {}
        try:
            payload = client.get_object(manifest_key)
            for line in payload.decode("utf-8").strip().split("\n"):
                if line.strip():
                    entry = json.loads(line)
                    existing[entry["partition_key"]] = entry
        except Exception:
            context.log.info("No existing members manifest — cold start")

        for entry in members_list_incremental:
            existing[entry["partition_key"]] = entry

        manifest = list(existing.values())
        lines = [json.dumps(e, default=str) for e in manifest]
        client.put_object(manifest_key, ("\n".join(lines) + "\n").encode("utf-8"))

        context.log.info(f"Members manifest: {len(manifest)} total ({len(members_list_incremental)} new/updated)")

        return Output(
            manifest,
            metadata={
                "total_members": len(manifest),
                "new_or_updated": len(members_list_incremental),
            },
        )
