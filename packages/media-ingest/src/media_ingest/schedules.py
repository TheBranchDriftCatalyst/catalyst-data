"""Schedules for media-ingest pipeline automation.

media_discovery_schedule: Materializes media_files + media_documents every 5
minutes so the media_document_sensor always sees freshly-discovered files.
Without this, new videos on NFS sit unprocessed until someone manually
materializes the discovery assets.
"""

from dagster import AssetKey, RunRequest, schedule


@schedule(
    cron_schedule="*/5 * * * *",
    name="media_discovery_schedule",
    description=(
        "Scan NFS for new media files every 5 minutes. "
        "Materializes media_files → media_documents so the "
        "media_document_sensor can detect and process new videos."
    ),
    default_status="RUNNING",
)
def media_discovery_schedule(_context):
    """Trigger media_files + media_documents materialization."""
    yield RunRequest(
        run_key=None,  # always run (idempotent — media_documents returns content_unchanged if nothing new)
        asset_selection=[
            AssetKey("media_files"),
            AssetKey("media_documents"),
        ],
    )
