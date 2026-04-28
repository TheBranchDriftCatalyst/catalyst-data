"""Shared fixtures for catalyst-exgraph tests."""

from __future__ import annotations

import pytest
from catalyst_exgraph.config import (
    PipelineConfig,
    StageConfig,
    default_pipeline_config,
    ner_stage_config,
    spo_stage_config,
)
from catalyst_exgraph.protocol import StageResult
from pydantic import BaseModel

# ── Dummy Pydantic schema for tests that don't need real extraction schemas ──


class DummyOutput(BaseModel):
    """Minimal Pydantic model for parameterizing StageConfig in tests."""

    items: list[str] = []


# ── StageConfig fixtures ────────────────────────────────────────────────────


@pytest.fixture
def dummy_stage_config() -> StageConfig:
    """A minimal StageConfig for generic tests."""
    return StageConfig(
        stage_name="dummy",
        extraction_schema=DummyOutput,
        prompt_id="test_prompt",
        validation_tool="test_validator",
        repair_prompt_id="test_repair",
    )


@pytest.fixture
def skipped_stage_config() -> StageConfig:
    """A StageConfig that is marked as skipped."""
    return StageConfig(
        stage_name="skipped_stage",
        extraction_schema=DummyOutput,
        prompt_id="test_prompt",
        validation_tool="test_validator",
        repair_prompt_id="test_repair",
        skip=True,
    )


@pytest.fixture
def ner_config() -> StageConfig:
    return ner_stage_config()


@pytest.fixture
def spo_config() -> StageConfig:
    return spo_stage_config()


@pytest.fixture
def pipeline_config() -> PipelineConfig:
    return default_pipeline_config()


# ── StageResult fixtures ────────────────────────────────────────────────────


@pytest.fixture
def populated_stage_result() -> StageResult:
    """A StageResult with realistic data in all fields."""
    r = StageResult()
    r.candidates = [{"text": "Alice", "type": "PERSON"}]
    r.accepted = [{"text": "Alice", "type": "PERSON", "span_start": 0, "span_end": 5}]
    r.validation = {"verdict": "pass", "errors": []}
    r.retry_count = 2
    r.audit_events = [{"event": "extracted", "model": "gpt-4o"}]
    r.status = "completed"
    r.error = ""
    return r


# ── Source text for span tests ──────────────────────────────────────────────


@pytest.fixture
def sample_source_text() -> str:
    return "Alice met Bob at the park. Later Alice called Bob."
