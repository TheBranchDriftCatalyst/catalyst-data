"""Per-asset materialization audit-log emission (CD-7pr0).

Verifies ``_emit_asset_materialization_events`` walks the run's event log,
emits one ``source=dagster, node_name=asset_materialized`` event per
``ASSET_MATERIALIZATION``, and reports any planned-but-missing assets
as ``asset_missing`` so a failed Dagster run still leaves an audit trail.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from dagster import (
    AssetKey,
    AssetMaterialization,
    DagsterEventType,
)
from dagster._core.events import (
    AssetMaterializationPlannedData,
    DagsterEvent,
    StepMaterializationData,
)
from dagster._core.events.log import EventLogEntry

from dagster_io.bench import event_store
from dagster_io.run_status_sensor import _emit_asset_materialization_events


def _make_log_entry(
    *,
    run_id: str,
    event_type: DagsterEventType,
    asset_key: AssetKey,
    partition: str | None = None,
    metadata: dict | None = None,
) -> EventLogEntry:
    """Mint an ``EventLogEntry`` mirroring what ``instance.all_logs`` returns.

    We construct the underlying ``DagsterEvent`` with the right
    ``event_type_value`` + ``event_specific_data`` payload so the
    sensor body's ``.dagster_event.event_type`` / ``.asset_key`` /
    ``.partition`` / ``.asset_materialization`` accessors resolve.
    """
    if event_type == DagsterEventType.ASSET_MATERIALIZATION:
        mat = AssetMaterialization(
            asset_key=asset_key,
            partition=partition,
            metadata=metadata or {},
        )
        specific: object = StepMaterializationData(materialization=mat)
    elif event_type == DagsterEventType.ASSET_MATERIALIZATION_PLANNED:
        specific = AssetMaterializationPlannedData(asset_key=asset_key, partition=partition)
    else:
        raise ValueError(f"unsupported event type: {event_type}")

    ev = DagsterEvent(
        event_type_value=event_type.value,
        job_name="test_job",
        event_specific_data=specific,
    )
    return EventLogEntry(
        error_info=None,
        level=20,
        user_message="",
        run_id=run_id,
        timestamp=0.0,
        dagster_event=ev,
    )


def _make_context(*, run_id: str, log_entries: list[EventLogEntry]) -> MagicMock:
    """Minimal RunStatusSensorContext stand-in — only ``dagster_run.run_id``
    and ``instance.all_logs`` are touched by the sensor body under test.
    """
    instance = MagicMock()
    instance.all_logs.return_value = log_entries
    dagster_run = MagicMock()
    dagster_run.run_id = run_id
    ctx = MagicMock()
    ctx.instance = instance
    ctx.dagster_run = dagster_run
    return ctx


def test_per_asset_events_emitted_with_partition_and_metadata(tmp_path: Path) -> None:
    """One ``asset_materialized`` event per materialization, doc_id=partition."""
    event_store.configure(run_id="test-run", run_dir=tmp_path)
    try:
        run_id = "abc123-dagster"
        entries = [
            _make_log_entry(
                run_id=run_id,
                event_type=DagsterEventType.ASSET_MATERIALIZATION,
                asset_key=AssetKey(["media_chunks"]),
                partition="demo-video",
                metadata={"row_count": 42, "size_bytes": 1024, "path": "s3://b/x.parquet"},
            ),
            _make_log_entry(
                run_id=run_id,
                event_type=DagsterEventType.ASSET_MATERIALIZATION,
                asset_key=AssetKey(["congress_chunks"]),
                partition="HR-119-1234",
                metadata={"row_count": 17},
            ),
        ]
        ctx = _make_context(run_id=run_id, log_entries=entries)

        _emit_asset_materialization_events("media_ingest", ctx)

        rows = [
            r
            for r in event_store.read_events_for_test()
            if r["source"] == "dagster" and r["node_name"] == "asset_materialized"
        ]
        assert len(rows) == 2

        media = next(r for r in rows if r["doc_id"] == "demo-video")
        assert media["code_location"] == "media_ingest"
        d = media["details"]
        assert d["asset_key"] == "media_chunks"
        assert d["partition_key"] == "demo-video"
        assert d["dagster_run_id"] == run_id
        assert d["metadata"]["row_count"] == 42
        assert d["metadata"]["size_bytes"] == 1024
        assert d["metadata"]["path"].startswith("s3://")
    finally:
        event_store.close()


def test_planned_but_missing_emits_asset_missing(tmp_path: Path) -> None:
    """A planned asset that never materialises shows up as ``asset_missing``."""
    event_store.configure(run_id="test-run-missing", run_dir=tmp_path)
    try:
        run_id = "fail-run"
        entries = [
            _make_log_entry(
                run_id=run_id,
                event_type=DagsterEventType.ASSET_MATERIALIZATION_PLANNED,
                asset_key=AssetKey(["media_chunks"]),
                partition="will-not-finish",
            ),
            _make_log_entry(
                run_id=run_id,
                event_type=DagsterEventType.ASSET_MATERIALIZATION_PLANNED,
                asset_key=AssetKey(["congress_chunks"]),
                partition="HR-119-9999",
            ),
            # Only one of the two planned assets actually materialises.
            _make_log_entry(
                run_id=run_id,
                event_type=DagsterEventType.ASSET_MATERIALIZATION,
                asset_key=AssetKey(["congress_chunks"]),
                partition="HR-119-9999",
            ),
        ]
        ctx = _make_context(run_id=run_id, log_entries=entries)

        _emit_asset_materialization_events("media_ingest", ctx)

        missing = [
            r
            for r in event_store.read_events_for_test()
            if r["source"] == "dagster" and r["node_name"] == "asset_missing"
        ]
        assert len(missing) == 1
        assert missing[0]["details"]["asset_key"] == "media_chunks"
        assert missing[0]["details"]["dagster_run_id"] == run_id
    finally:
        event_store.close()
