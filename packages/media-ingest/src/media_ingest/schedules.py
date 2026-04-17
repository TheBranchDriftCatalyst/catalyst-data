"""Schedules for media-ingest pipeline automation.

media_discovery_schedule: Materializes media_files + media_documents every 5
minutes so the media_document_sensor always sees freshly-discovered files.
Without this, new videos on NFS sit unprocessed until someone manually
materializes the discovery assets.
"""

from dagster import AssetSelection, DefaultScheduleStatus, ScheduleDefinition, define_asset_job

media_discovery_job = define_asset_job(
    name="media_discovery_job",
    selection=AssetSelection.assets(
        "media_files",
        "media_metadata",
        "media_documents",
    ),
    tags={"dagster/priority": "10"},
    description="Scan NFS → ffprobe metadata → register documents. Transcode runs independently.",
)

media_discovery_schedule = ScheduleDefinition(
    name="media_discovery_schedule",
    job=media_discovery_job,
    cron_schedule="*/5 * * * *",
    description=(
        "Scan NFS for new media files every 5 minutes. "
        "Materializes media_files → media_documents so the "
        "media_document_sensor can detect and process new videos."
    ),
    default_status=DefaultScheduleStatus.RUNNING,
)
