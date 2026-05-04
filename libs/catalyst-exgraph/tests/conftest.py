"""Shared fixtures for catalyst-exgraph tests."""

from __future__ import annotations

from typing import Any

import pytest
from catalyst_contracts.models.extraction_output import (
    MentionCandidate,
    MentionExtractionResult,
    PropositionCandidate,
    PropositionExtractionResult,
)
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


# ── Mock extraction client ──────────────────────────────────────────────────


class MockExtractionClient:
    """Mock extraction client that returns canned structured output.

    Satisfies ExtractionClient protocol. Configure with mention/proposition
    data and it returns proper Pydantic models from structured_output().
    """

    model: str = "mock-model"
    structured_method: str = "mock"

    def __init__(
        self,
        mentions: list[dict] | None = None,
        propositions: list[dict] | None = None,
    ) -> None:
        self._mention_data = mentions or []
        self._proposition_data = propositions or []
        self.structured_calls: list[tuple[type, list]] = []

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        self.structured_calls.append((schema, messages))

        if schema is MentionExtractionResult:
            objs = [MentionCandidate(**m) for m in self._mention_data]
            return MentionExtractionResult(mentions=objs)
        elif schema is PropositionExtractionResult:
            objs = [PropositionCandidate(**p) for p in self._proposition_data]
            return PropositionExtractionResult(propositions=objs)

        # Fallback: guess from message content
        msg_text = " ".join(str(m) for m in messages).lower()
        if "proposition" in msg_text or "triple" in msg_text:
            objs = [PropositionCandidate(**p) for p in self._proposition_data]
            return PropositionExtractionResult(propositions=objs)

        objs = [MentionCandidate(**m) for m in self._mention_data]
        return MentionExtractionResult(mentions=objs)


class MockMCPClient:
    """Mock MCP validator that returns configurable verdicts.

    Supports static verdicts, per-call sequences (for repair testing),
    and callable responses.
    """

    def __init__(
        self,
        verdict: str = "valid",
        valid_items: list[int] | None = None,
        errors: list[dict] | None = None,
        verdicts: list[str] | None = None,
    ) -> None:
        self._static_verdict = verdict
        self._valid_items = valid_items or []
        self._errors = errors or []
        self._verdicts = verdicts  # Sequential verdicts (pops from front)
        self.call_count = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        self.call_count += 1

        # Use sequential verdict if available
        if self._verdicts:
            verdict = self._verdicts.pop(0) if self._verdicts else self._static_verdict
        else:
            verdict = self._static_verdict

        items = arguments.get("mentions", arguments.get("propositions", arguments.get("items", [])))
        valid_count = len(items) if verdict == "valid" else len(self._valid_items)
        invalid_count = len(items) - valid_count if verdict != "valid" else 0

        return {
            "verdict": verdict,
            "valid_items": list(range(len(items))) if verdict == "valid" else self._valid_items,
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "errors": self._errors,
        }


# ── Convenience fixtures ────────────────────────────────────────────────────


SAMPLE_MENTIONS = [
    {
        "text": "Acme Corp",
        "mention_type": "ORG",
        "span_start": 0,
        "span_end": 9,
        "confidence": 1.0,
    },
    {
        "text": "John Smith",
        "mention_type": "PERSON",
        "span_start": 25,
        "span_end": 35,
        "confidence": 0.95,
    },
]

SAMPLE_PROPOSITIONS = [
    {
        "subject": "John Smith",
        "predicate": "works_for",
        "object": "Acme Corp",
        "confidence": 0.9,
        "evidence": "John Smith works for Acme Corp",
    },
]

SAMPLE_TEXT = "Acme Corp announced that John Smith was promoted to CEO."


@pytest.fixture
def mock_extraction_client() -> MockExtractionClient:
    return MockExtractionClient(mentions=SAMPLE_MENTIONS)


@pytest.fixture
def mock_mcp_valid() -> MockMCPClient:
    return MockMCPClient(verdict="valid")


@pytest.fixture
def mock_mcp_invalid() -> MockMCPClient:
    return MockMCPClient(
        verdict="invalid",
        errors=[{"field": "span_start", "message": "span mismatch"}],
    )


@pytest.fixture
def mock_mcp_ambiguous() -> MockMCPClient:
    return MockMCPClient(
        verdict="ambiguous",
        valid_items=[0],
        errors=[{"field": "span_end", "message": "off by one"}],
    )


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


# ── event_store fixture — configure the writer to a temp dir ─────────────────


@pytest.fixture(autouse=True)
def configure_event_store(tmp_path):
    """Configure event_store to a temp run directory for every test.

    Closes any leaked module-global writer before and after the test so
    test isolation is maintained. ``autouse=True`` so every test in the
    package gets this without opting in. Tests that need to inspect what
    was emitted call ``event_store.read_events_for_test()`` to drain the
    parquet shard back to a list of dicts.
    """
    from dagster_io.bench import event_store

    event_store.close()
    event_store.configure(run_id="test-run", run_dir=tmp_path)

    yield

    event_store.close()
