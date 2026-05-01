"""Detect whether we're running inside a Dagster execution context.

Used to gate eager OTEL telemetry initialization — production Dagster
deployments get telemetry; CLI scripts and dev tooling get silent no-ops.
The previous behavior was to always spin up the OTLP gRPC exporter to
``alloy.monitoring.svc.cluster.local:4317``, which floods Mac dev shells with
``StatusCode.UNAVAILABLE`` retries because the in-cluster collector isn't
reachable from outside the talos cluster.
"""

from __future__ import annotations

import os

# Env vars Dagster sets when it's running an asset / sensor / schedule /
# code-server. Presence of ANY of these means we're inside Dagster.
_DAGSTER_ENV_SIGNALS = (
    "DAGSTER_RUN_JOB_NAME",  # set in step pods during materialization
    "DAGSTER_RUN_ID",  # set in any Dagster process executing a run
    "DAGSTER_IS_CODE_SERVER",  # set in code-server pods
    "DAGSTER_HOME",  # set when dagster dev / dagster-webserver runs
)

# Explicit override — set to "1"/"true" to FORCE telemetry on outside Dagster
# (e.g. a developer wants to test the metrics path locally).
_TELEMETRY_OVERRIDE_ENV = "CATALYST_TELEMETRY"

_TRUTHY = {"1", "true", "yes", "on"}


def in_dagster_context() -> bool:
    """True if running inside a Dagster execution context.

    Checks for any of: ``DAGSTER_RUN_JOB_NAME`` (step pods),
    ``DAGSTER_RUN_ID`` (any executing run), ``DAGSTER_IS_CODE_SERVER``
    (code-server pods), ``DAGSTER_HOME`` (``dagster dev`` /
    ``dagster-webserver``).
    """
    return any(os.environ.get(k) for k in _DAGSTER_ENV_SIGNALS)


def telemetry_enabled() -> bool:
    """Should OTEL exporters initialize?

    Returns True when:
      - Running inside a Dagster context (auto-detected), OR
      - ``CATALYST_TELEMETRY=1`` (or true/yes/on) is explicitly set

    Returns False when:
      - Running outside Dagster with no override (CLI scripts default off), OR
      - Existing kill-switches set: ``OTEL_METRICS_EXPORTER=none``,
        ``TRACING_ENABLED=false`` (these are authoritative — even inside
        Dagster they suppress).
    """
    if os.environ.get("OTEL_METRICS_EXPORTER", "").lower() == "none":
        return False
    if os.environ.get("TRACING_ENABLED", "true").lower() == "false":
        return False
    if os.environ.get(_TELEMETRY_OVERRIDE_ENV, "").lower() in _TRUTHY:
        return True
    return in_dagster_context()
