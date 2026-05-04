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
