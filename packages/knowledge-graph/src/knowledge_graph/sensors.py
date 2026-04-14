"""Sensors for knowledge-graph platinum layer automation.

platinum_resolution_sensor: Watches for new media_entity_candidates
materializations and triggers canonical_entities + entity_alignments
so the knowledge graph stays up-to-date as new documents flow through.
"""

from dagster import (
    AssetKey,
    DefaultSensorStatus,
    EventLogEntry,
    RunRequest,
    SensorEvaluationContext,
    asset_sensor,
)

from dagster_io.logging import get_logger

logger = get_logger(__name__)


@asset_sensor(
    asset_key=AssetKey("media_entity_candidates"),
    name="platinum_resolution_sensor",
    description=(
        "Triggers canonical_entities + entity_alignments when new "
        "media_entity_candidates are materialized. Keeps the platinum "
        "knowledge graph up-to-date as new documents are processed."
    ),
    minimum_interval_seconds=300,
    default_status=DefaultSensorStatus.RUNNING,
)
def platinum_resolution_sensor(context: SensorEvaluationContext, asset_event: EventLogEntry):
    """Trigger platinum resolution after gold entity candidates land."""
    logger.info(
        "platinum_resolution_sensor: new media_entity_candidates detected, "
        "triggering canonical_entities + entity_alignments"
    )
    yield RunRequest(
        run_key=f"platinum_resolution_{context.cursor}",
        asset_selection=[
            AssetKey("canonical_entities"),
            AssetKey("entity_alignments"),
        ],
    )
