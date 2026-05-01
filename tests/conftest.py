"""Shared Dagster test fixtures for catalyst-data pipelines."""

import contextlib
import os

# Suppress OTEL exports BEFORE any dagster_io imports initialize the meter.
# Must be set at module level, not in a fixture (too late — meters init at import).
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")
os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")

import pytest
from dagster import build_asset_context


def _safe_addoption(parser, *args, **kwargs):
    with contextlib.suppress(ValueError):
        parser.addoption(*args, **kwargs)


def pytest_addoption(parser):
    _safe_addoption(
        parser,
        "--regen",
        action="store_true",
        default=False,
        help="Force regenerate all extraction fixtures (ignore cached results)",
    )
    _safe_addoption(
        parser,
        "--timeout",
        type=int,
        default=None,
        help="Override per-model timeout in seconds (default: 300 from benchmark_config)",
    )
    _safe_addoption(
        parser,
        "--audit-log",
        action="store_true",
        default=False,
        help="Save full structured audit logs per model to .test-output/media-ingest/audit-logs/",
    )


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch):
    """Set safe defaults so tests never hit real APIs or cluster endpoints."""
    monkeypatch.setenv("CONGRESS_API_KEY", "test-key")
    monkeypatch.setenv("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("DAGSTER_S3_ACCESS_KEY", "test")
    monkeypatch.setenv("DAGSTER_S3_SECRET_KEY", "test")
    monkeypatch.setenv("DAGSTER_S3_BUCKET", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    # Suppress OTEL metric export retries to unreachable cluster endpoints
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")


@pytest.fixture
def asset_context():
    """Build a Dagster asset context for testing."""
    return build_asset_context()
