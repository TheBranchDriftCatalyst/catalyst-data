"""Schedules for congress-data pipeline automation.

bills_discovery_schedule: every 4h — materializes head assets so sensors see fresh data.
members_discovery_schedule: daily — members change less frequently.

Pattern mirrors media_ingest/schedules.py.
"""

from dagster import AssetSelection, DefaultScheduleStatus, ScheduleDefinition, define_asset_job

# ── Bills discovery ──────────────────────────────────────────────────────────

bills_discovery_job = define_asset_job(
    name="bills_discovery_job",
    selection=AssetSelection.assets(
        "bills_list_incremental",
        "bills_manifest",
    ),
    description="Incremental bills list pull → manifest update for sensor consumption.",
)

bills_discovery_schedule = ScheduleDefinition(
    name="bills_discovery_schedule",
    job=bills_discovery_job,
    cron_schedule="0 */4 * * *",
    description="Pull updated bills from Congress.gov every 4 hours during session.",
    default_status=DefaultScheduleStatus.RUNNING,
)

# ── Members discovery ────────────────────────────────────────────────────────

members_discovery_job = define_asset_job(
    name="members_discovery_job",
    selection=AssetSelection.assets(
        "members_list_incremental",
        "members_manifest",
    ),
    description="Incremental members list pull → manifest update.",
)

members_discovery_schedule = ScheduleDefinition(
    name="members_discovery_schedule",
    job=members_discovery_job,
    cron_schedule="0 6 * * *",
    description="Pull updated members from Congress.gov daily at 06:00 UTC.",
    default_status=DefaultScheduleStatus.RUNNING,
)
