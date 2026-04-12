"""Tests for ``dagster_io.metrics.push_metrics`` — the Prometheus pushgateway
export path that replaced the lossy hand-rolled OTLP conversion (CD-jsb).

Covers:
- Happy path: push_to_gateway called once with the right gateway / job /
  grouping_key derived from env vars.
- Exception safety: push_metrics must swallow network / gateway errors
  because it runs in atexit and SIGTERM handlers where raising is worse
  than losing a single metric push.
- Scheme stripping: both ``http://host:port`` and bare ``host:port`` forms
  of PROMETHEUS_PUSHGATEWAY_URL should result in a scheme-less gateway arg.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from dagster_io import metrics as metrics_module


@pytest.fixture(autouse=True)
def _clean_metrics_env(monkeypatch):
    """Strip any ambient metrics env vars so each test starts from a clean slate."""
    for var in (
        "PROMETHEUS_PUSHGATEWAY_URL",
        "DAGSTER_CODE_LOCATION",
        "DAGSTER_RUN_ID",
        "DAGSTER_STEP_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_push_metrics_calls_pushgateway_with_expected_args(monkeypatch):
    """Happy path: env vars → gateway + grouping_key + job wiring."""
    monkeypatch.setenv("DAGSTER_CODE_LOCATION", "test_loc")
    monkeypatch.setenv("DAGSTER_RUN_ID", "test_run_123")
    monkeypatch.setenv("DAGSTER_STEP_KEY", "test_step")
    monkeypatch.setenv("PROMETHEUS_PUSHGATEWAY_URL", "http://fake:9091")

    with patch.object(metrics_module, "push_to_gateway") as mock_push:
        metrics_module.push_metrics(job_name="test_job")

    assert mock_push.call_count == 1
    _, kwargs = mock_push.call_args
    assert kwargs["gateway"] == "fake:9091"  # scheme stripped
    assert kwargs["job"] == "test_job"
    assert kwargs["registry"] is metrics_module.REGISTRY
    assert kwargs["timeout"] == 10

    grouping_key = kwargs["grouping_key"]
    assert grouping_key["code_location"] == "test_loc"
    assert grouping_key["run_id"] == "test_run_123"
    assert grouping_key["step_key"] == "test_step"


def test_push_metrics_swallows_exceptions(monkeypatch):
    """push_metrics runs in atexit / SIGTERM — it MUST NOT raise, even when
    the gateway is unreachable, returns an HTTP error, or the network blows up.
    """
    monkeypatch.setenv("DAGSTER_CODE_LOCATION", "test_loc")
    monkeypatch.setenv("DAGSTER_RUN_ID", "test_run_123")
    monkeypatch.setenv("PROMETHEUS_PUSHGATEWAY_URL", "http://nonexistent.invalid:9091")

    def _boom(*args, **kwargs):
        raise ConnectionError("pushgateway not reachable — simulated")

    with patch.object(metrics_module, "push_to_gateway", side_effect=_boom):
        # Must not raise.
        metrics_module.push_metrics(job_name="test_job")


@pytest.mark.parametrize(
    "raw_url, expected_gateway",
    [
        ("http://fake:9091", "fake:9091"),
        ("https://fake:9091", "fake:9091"),
        ("fake:9091", "fake:9091"),
        (
            "http://prometheus-pushgateway.monitoring.svc.cluster.local:9091",
            "prometheus-pushgateway.monitoring.svc.cluster.local:9091",
        ),
        (
            "prometheus-pushgateway.monitoring.svc.cluster.local:9091",
            "prometheus-pushgateway.monitoring.svc.cluster.local:9091",
        ),
    ],
)
def test_push_metrics_strips_scheme(monkeypatch, raw_url, expected_gateway):
    """Both ``http://host:port`` and bare ``host:port`` forms of the env var
    should resolve to the same scheme-less gateway passed to push_to_gateway.
    """
    monkeypatch.setenv("DAGSTER_CODE_LOCATION", "test_loc")
    monkeypatch.setenv("DAGSTER_RUN_ID", "test_run_123")
    monkeypatch.setenv("PROMETHEUS_PUSHGATEWAY_URL", raw_url)

    with patch.object(metrics_module, "push_to_gateway") as mock_push:
        metrics_module.push_metrics(job_name="test_job")

    assert mock_push.call_count == 1
    _, kwargs = mock_push.call_args
    assert kwargs["gateway"] == expected_gateway


def test_push_metrics_defaults_when_env_missing(monkeypatch):
    """With no env vars set, grouping_key should use documented fallbacks:
    code_location=unknown, run_id=pid-<pid>, step_key=none.
    """
    # All env vars pre-stripped by the autouse fixture.

    with patch.object(metrics_module, "push_to_gateway") as mock_push:
        metrics_module.push_metrics()

    assert mock_push.call_count == 1
    _, kwargs = mock_push.call_args

    # Default gateway (scheme stripped)
    assert kwargs["gateway"] == ("prometheus-pushgateway.monitoring.svc.cluster.local:9091")
    assert kwargs["job"] == "dagster_step"

    grouping_key = kwargs["grouping_key"]
    assert grouping_key["code_location"] == "unknown"
    assert grouping_key["run_id"] == f"pid-{os.getpid()}"
    assert grouping_key["step_key"] == "none"
