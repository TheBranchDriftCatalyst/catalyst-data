"""Shared Dagster test fixtures for catalyst-data pipelines."""

import os

import pytest
from dagster import build_asset_context


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch):
    """Set safe defaults so tests never hit real APIs."""
    monkeypatch.setenv("CONGRESS_API_KEY", "test-key")
    monkeypatch.setenv("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("DAGSTER_S3_ACCESS_KEY", "test")
    monkeypatch.setenv("DAGSTER_S3_SECRET_KEY", "test")
    monkeypatch.setenv("DAGSTER_S3_BUCKET", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


@pytest.fixture
def asset_context():
    """Build a Dagster asset context for testing."""
    return build_asset_context()
