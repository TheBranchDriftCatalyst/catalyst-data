"""Shared RunStatusSensor factory for catalyst-data Dagster code locations.

Every code location in the monorepo emits the same run-level observability
signals on run completion: outcome counter, wall-clock duration histogram,
and per-asset last-materialized-timestamp gauge. Before this existed the
Grafana dashboard had zero run-level metrics (see April 11 cognitive
council review / CD-59v) and operators could not answer "did anything run
today?" from metrics alone — the 48h incident chain we recovered from was
hard to diagnose specifically because of that gap.

This module mirrors ``dagster_io.executor.make_k8s_executor``: one factory
that takes the code-location name and returns fully-configured Dagster
sensors that each code location registers in its ``Definitions``.

Usage (per code location)::

    from dagster import Definitions
    from dagster_io import make_run_status_sensor

    defs = Definitions(
        assets=[...],
        sensors=[
            media_document_sensor,
            *make_run_status_sensor("media_ingest"),
        ],
        ...
    )

Why a list instead of a single definition:
    Dagster's ``@run_status_sensor`` decorator takes **one**
    ``DagsterRunStatus`` value per sensor. To cover SUCCESS, FAILURE, and
    CANCELED we therefore need three sensor definitions sharing the same
    metric-emission body. Returning them as a list keeps the call site to
    one line per code location (``*make_run_status_sensor(...)``) and
    sidesteps the need for a separate "unpack" helper.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from dagster import (
    DagsterEventType,
    DagsterRunStatus,
    RunStatusSensorContext,
    RunStatusSensorDefinition,
    run_status_sensor,
)

from dagster_io import event_store
from dagster_io.metrics import (
    ASSET_LAST_MATERIALIZED_TIMESTAMP_SECONDS,
    DAGSTER_RUN_DURATION_SECONDS,
    DAGSTER_RUN_STATUS_TOTAL,
)

logger = logging.getLogger(__name__)


# Map the three terminal DagsterRunStatus values to the Prometheus label
# value used on DAGSTER_RUN_STATUS_TOTAL. Doing this explicitly (rather
# than ``.name.lower()``) keeps the label surface stable across Dagster
# version bumps even if the enum's ``repr`` / ``name`` changes.
_STATUS_LABELS: dict[DagsterRunStatus, str] = {
    DagsterRunStatus.SUCCESS: "success",
    DagsterRunStatus.FAILURE: "failure",
    DagsterRunStatus.CANCELED: "canceled",
}


def _emit_run_metrics(code_location: str, context: RunStatusSensorContext) -> None:
    """Emit run-level counter + duration histogram + freshness gauges.

    Shared by all three per-status sensor bodies so the emission logic
    lives in exactly one place.
    """
    dagster_run = context.dagster_run
    status = dagster_run.status
    status_label = _STATUS_LABELS.get(status)
    if status_label is None:
        # Non-terminal status — shouldn't happen because the decorator
        # filters on the monitored run_status, but guard anyway so a
        # future enum addition doesn't blow up prometheus with an
        # ``InvalidLabelValue``.
        logger.warning(
            "make_run_status_sensor: ignoring non-terminal status %s for run %s",
            status,
            dagster_run.run_id,
        )
        return

    # ``job_name`` can be missing for some ephemeral contexts; coerce to
    # a stable label so we never blow up the scraper with a label
    # cardinality error.
    job_name = dagster_run.job_name or "<unknown>"

    DAGSTER_RUN_STATUS_TOTAL.labels(
        code_location=code_location,
        job_name=job_name,
        status=status_label,
    ).inc()

    # Wall-clock duration. Dagster's ``DagsterRun`` exposes epoch-float
    # ``start_time`` / ``end_time`` on terminal runs. Either can be None
    # (e.g. when a run was canceled before starting), in which case we
    # skip the histogram observation rather than record a garbage value.
    start_time = getattr(dagster_run, "start_time", None)
    end_time = getattr(dagster_run, "end_time", None)
    if start_time is not None and end_time is not None and end_time >= start_time:
        DAGSTER_RUN_DURATION_SECONDS.labels(
            code_location=code_location,
            job_name=job_name,
        ).observe(end_time - start_time)

    # Freshness gauge: only on SUCCESS do we touch the last-materialized
    # timestamp. FAILURE / CANCELED runs leave the gauge untouched so
    # ``time() - gauge`` accurately reflects staleness from the last
    # **successful** materialization.
    if status is DagsterRunStatus.SUCCESS:
        asset_selection = getattr(dagster_run, "asset_selection", None) or ()
        now = time.time()
        for asset_key in asset_selection:
            label = asset_key.to_user_string() if hasattr(asset_key, "to_user_string") else str(asset_key)
            ASSET_LAST_MATERIALIZED_TIMESTAMP_SECONDS.labels(
                code_location=code_location,
                asset_key=label,
            ).set(now)

    # Emit to the bench audit log so Dagster runs land on the same
    # timeline as harness/exgraph/langgraph events. Skipped silently when
    # event_store isn't configured (e.g. dagster-webserver outside a
    # benchmark context).
    if event_store.is_configured():
        details: dict[str, Any] = {
            "job_name": job_name,
            "run_id": dagster_run.run_id,
        }
        if start_time is not None and end_time is not None and end_time >= start_time:
            details["duration_s"] = end_time - start_time
        event_store.append(
            source="dagster",
            node_name=job_name,
            status=status_label,
            code_location=code_location,
            details=details,
        )
        # Per-asset materialization events — gives StateInspector +
        # cross-run reflexion full Dagster lineage. Without these the
        # bench timeline only sees the run-level summary above and can't
        # answer "which materialization produced these chunks?".
        # Wrapped in try/except so a Dagster API surface drift can never
        # take out the run-status emission above.
        try:
            _emit_asset_materialization_events(code_location, context)
        except Exception as exc:  # noqa: BLE001 — never let auditing kill a run sensor
            logger.warning(
                "make_run_status_sensor: per-asset event emission failed for run %s: %s",
                dagster_run.run_id,
                exc,
            )


def _emit_asset_materialization_events(
    code_location: str,
    context: RunStatusSensorContext,
) -> None:
    """Walk the run's event log and emit one bench event per materialization.

    Source: ``context.instance.all_logs(run_id, of_type=ASSET_MATERIALIZATION)``
    returns one ``EventLogEntry`` per materialization in the run. Each entry
    carries the asset_key, partition (if partitioned), and the full
    ``AssetMaterialization`` payload (description + metadata dict) emitted
    by the asset / IOManager. We forward all of that into bench
    ``details`` so downstream consumers (StateInspector upstream tab,
    reflexion harvester) have full provenance without having to re-query
    the Dagster instance.

    Why ``doc_id = partition_key`` for partitioned assets: bench events
    are hive-partitioned by ``doc_id`` and the StateInspector's primary
    filter axis is doc. Mapping a partitioned materialization to its
    partition key lands the event in the same parquet shard as the
    harness events for that doc — single read, single panel. Unpartitioned
    materializations land in the ``__run__`` synthetic partition with the
    run-level summary above.
    """
    dagster_run = context.dagster_run
    instance = context.instance
    run_id = dagster_run.run_id

    # ``all_logs`` is the runtime-filtered event log. ``of_type`` accepts
    # either a single DagsterEventType or an iterable; we want both
    # MATERIALIZATION (success) and MATERIALIZATION_PLANNED (so failures
    # show up as "asset_planned but never materialized" in the audit).
    entries = list(
        instance.all_logs(
            run_id,
            of_type={DagsterEventType.ASSET_MATERIALIZATION, DagsterEventType.ASSET_MATERIALIZATION_PLANNED},
        )
    )

    materialized_keys: set[str] = set()
    for entry in entries:
        ev = entry.dagster_event
        if ev is None or ev.asset_key is None:
            continue
        asset_key_str = ev.asset_key.to_user_string()

        if ev.event_type == DagsterEventType.ASSET_MATERIALIZATION_PLANNED:
            # We emit one of these at planned-time; if it materializes
            # we'll overwrite below. Useful when a run fails partway —
            # the planned-but-missing assets are visible.
            materialization_details: dict[str, Any] = {
                "asset_key": asset_key_str,
                "dagster_run_id": run_id,
                "stage": "planned",
            }
            event_store.append(
                source="dagster",
                node_name="asset_planned",
                status="planned",
                code_location=code_location,
                doc_id=None,  # partition_key not known until materialization
                details=materialization_details,
            )
            continue

        # ASSET_MATERIALIZATION
        materialized_keys.add(asset_key_str)
        # ``DagsterEvent.asset_materialization`` is an unbound method in
        # current Dagster releases, not a property — pull the
        # materialization off the event-specific payload directly.
        esd = ev.event_specific_data
        mat = getattr(esd, "materialization", None) if esd is not None else None
        partition_key = ev.partition  # str | None
        # ``mat.metadata`` is a dict of MetadataValue — coerce each to a
        # JSON-friendly primitive so the parquet round-trip works without
        # custom encoders. ``MetadataValue`` exposes ``.value`` which is
        # already a primitive for the common types (Path, Int, Float,
        # Text, Url) and a dict for Json.
        meta_out: dict[str, Any] = {}
        if mat is not None and getattr(mat, "metadata", None):
            for k, v in mat.metadata.items():
                meta_out[str(k)] = _coerce_metadata_value(v)

        details = {
            "asset_key": asset_key_str,
            "partition_key": partition_key,
            "dagster_run_id": run_id,
            "description": getattr(mat, "description", None) if mat else None,
            "metadata": meta_out,
            "ts": entry.timestamp,
        }
        event_store.append(
            source="dagster",
            node_name="asset_materialized",
            status="ok",
            code_location=code_location,
            # Hive-partition by partition_key when the asset is partitioned;
            # unpartitioned assets land in __run__ alongside the run summary.
            doc_id=partition_key,
            details=details,
        )

    # Surface assets that were planned but never materialized — typical
    # for FAILURE / CANCELED runs. ``planned but missing`` is the cheap
    # signal an operator wants in StateInspector.
    planned_keys = {
        ev.dagster_event.asset_key.to_user_string()
        for ev in entries
        if ev.dagster_event
        and ev.dagster_event.event_type == DagsterEventType.ASSET_MATERIALIZATION_PLANNED
        and ev.dagster_event.asset_key is not None
    }
    for missing in planned_keys - materialized_keys:
        event_store.append(
            source="dagster",
            node_name="asset_missing",
            status="error",
            code_location=code_location,
            doc_id=None,
            details={
                "asset_key": missing,
                "dagster_run_id": run_id,
                "reason": "planned but not materialized (run terminated)",
            },
        )


def _coerce_metadata_value(v: Any) -> Any:
    """Best-effort flatten of Dagster ``MetadataValue`` → JSON primitive.

    Avoids importing concrete MetadataValue subclasses (the public-API
    set has shifted across Dagster versions). The shared protocol all
    of them honour is ``.value`` returning the underlying primitive
    (int / float / str / Path / dict / list), so we read that and
    fall back to ``str(v)`` if missing — losing nothing but the
    typed wrapper.
    """
    val = getattr(v, "value", v)
    # ``Path`` and other arbitrary objects → string. JSON-native types
    # (dict / list / int / float / str / bool / None) pass through.
    if isinstance(val, (str, int, float, bool)) or val is None:
        return val
    if isinstance(val, (list, tuple)):
        return [_coerce_metadata_value(x) for x in val]
    if isinstance(val, dict):
        return {str(k): _coerce_metadata_value(x) for k, x in val.items()}
    return str(val)


def make_run_status_sensor(code_location: str) -> list[RunStatusSensorDefinition]:
    """Build the DAG-health ``RunStatusSensorDefinition`` trio for a code location.

    Args:
        code_location: The Dagster code location name (e.g.
            ``"media_ingest"``, ``"knowledge_graph"``). Used as a static
            Prometheus label on every emitted metric so the Grafana
            dashboard can group / filter by code location and so multiple
            code locations can coexist in the same Mimir workspace without
            name collisions.

    Why this exists:
        Before CD-59v there were zero run-level metrics in
        ``libs/dagster-io/src/dagster_io/metrics.py``. The dashboard could
        only answer "how long did asset X take?" but could not answer
        "did anything run today?" or "how fresh is asset X?". This factory
        wires the three run-level metrics declared in ``metrics.py``
        (``DAGSTER_RUN_STATUS_TOTAL``, ``DAGSTER_RUN_DURATION_SECONDS``,
        ``ASSET_LAST_MATERIALIZED_TIMESTAMP_SECONDS``) into every code
        location with one line::

            sensors=[..., *make_run_status_sensor("my_code_location")]

        The fourth DAG health metric — ``DAGSTER_SENSOR_TICK_TOTAL`` —
        is defined in ``metrics.py`` but NOT emitted from this factory.
        A run-status sensor body cannot observe its own ticks without
        circular logic, and wiring a separate instance-level sensor /
        instance hook to emit sensor-tick counts is left as a follow-up.

    Returns:
        A list of ``RunStatusSensorDefinition`` objects — one per
        terminal ``DagsterRunStatus`` (SUCCESS, FAILURE, CANCELED). Splat
        the result into ``Definitions(sensors=[...])``. Each sensor is
        named ``f"run_status_{code_location}_sensor"`` so that
        multiple code locations can coexist without sensor name collisions.
    """
    base_name = f"run_status_{code_location}_sensor"

    @run_status_sensor(
        run_status=DagsterRunStatus.SUCCESS,
        name=f"{base_name}__success",
        description=(
            f"Emits catalyst_dagster_run_status_total{{status=success}}, "
            f"catalyst_dagster_run_duration_seconds, and "
            f"catalyst_asset_last_materialized_timestamp_seconds for "
            f"successful runs in code_location={code_location}."
        ),
    )
    def _on_success(context: RunStatusSensorContext) -> None:
        _emit_run_metrics(code_location, context)

    @run_status_sensor(
        run_status=DagsterRunStatus.FAILURE,
        name=f"{base_name}__failure",
        description=(
            f"Emits catalyst_dagster_run_status_total{{status=failure}} "
            f"and catalyst_dagster_run_duration_seconds for failed runs "
            f"in code_location={code_location}."
        ),
    )
    def _on_failure(context: RunStatusSensorContext) -> None:
        _emit_run_metrics(code_location, context)

    @run_status_sensor(
        run_status=DagsterRunStatus.CANCELED,
        name=f"{base_name}__canceled",
        description=(
            f"Emits catalyst_dagster_run_status_total{{status=canceled}} "
            f"and catalyst_dagster_run_duration_seconds for canceled runs "
            f"in code_location={code_location}."
        ),
    )
    def _on_canceled(context: RunStatusSensorContext) -> None:
        _emit_run_metrics(code_location, context)

    return [_on_success, _on_failure, _on_canceled]
