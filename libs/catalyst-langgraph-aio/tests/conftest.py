"""Shared test fixtures for catalyst-langgraph-aio."""

from __future__ import annotations

import json
from typing import Any

import pytest

from catalyst_langgraph.clients.mcp import MockMCPClient

from catalyst_contracts.models.extraction_output import (
    MentionCandidate,
    MentionExtractionResult,
    PropositionCandidate,
    PropositionExtractionResult,
)


class MockLLMClient:
    """Mock LLM client for testing that returns configurable responses.

    Supports both ``complete()`` (legacy) and ``structured_output()`` calls.
    For ``structured_output()``, returns a proper Pydantic model instance
    matching the requested schema.
    """

    def __init__(self) -> None:
        self.complete_responses: dict[str, str] = {}
        self.structured_responses: dict[str, Any] = {}
        self.complete_calls: list[tuple[str, str]] = []
        self.structured_calls: list[tuple[type, list]] = []
        self._mention_data: list[dict] | None = None
        self._proposition_data: list[dict] | None = None

    def set_complete_response(self, key: str, response: str) -> None:
        self.complete_responses[key] = response

    def set_default_mentions(self, mentions: list[dict]) -> None:
        self._mention_data = mentions
        self.complete_responses["_default_mentions"] = json.dumps(
            {"mentions": mentions}
        )

    def set_default_propositions(self, propositions: list[dict]) -> None:
        self._proposition_data = propositions
        self.complete_responses["_default_propositions"] = json.dumps(
            {"propositions": propositions}
        )

    async def complete(self, prompt: str, *, system: str = "") -> str:
        self.complete_calls.append((prompt, system))
        # Check for exact prompt matches first
        for key, resp in self.complete_responses.items():
            if key.startswith("_default_"):
                continue
            if key in prompt:
                return resp

        # Check for default responses based on system prompt content.
        # Check propositions first — proposition prompts also contain "mention"
        # so checking mention first would incorrectly match.
        if "proposition" in system.lower() or "triple" in system.lower():
            if "_default_propositions" in self.complete_responses:
                return self.complete_responses["_default_propositions"]
        if "entity" in system.lower() or "mention" in system.lower():
            if "_default_mentions" in self.complete_responses:
                return self.complete_responses["_default_mentions"]

        # Fallback — return empty
        return json.dumps({"mentions": [], "propositions": []})

    async def structured_output(self, schema: type, messages: list) -> Any:
        self.structured_calls.append((schema, messages))

        # Determine if this is a mention or proposition extraction based on
        # the requested schema type
        if schema is MentionExtractionResult:
            data = self._mention_data or []
            mention_objects = [MentionCandidate(**m) for m in data]
            return MentionExtractionResult(mentions=mention_objects)
        elif schema is PropositionExtractionResult:
            data = self._proposition_data or []
            prop_objects = [PropositionCandidate(**p) for p in data]
            return PropositionExtractionResult(propositions=prop_objects)

        # Fallback for unknown schemas — check message content for hints
        msg_text = " ".join(str(m) for m in messages).lower()
        if "proposition" in msg_text or "triple" in msg_text:
            data = self._proposition_data or []
            prop_objects = [PropositionCandidate(**p) for p in data]
            return PropositionExtractionResult(propositions=prop_objects)

        data = self._mention_data or []
        mention_objects = [MentionCandidate(**m) for m in data]
        return MentionExtractionResult(mentions=mention_objects)


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest.fixture
def mock_mcp() -> MockMCPClient:
    return MockMCPClient()


@pytest.fixture
def sample_mentions() -> list[dict]:
    return [
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
            "span_start": 20,
            "span_end": 30,
            "confidence": 1.0,
        },
    ]


@pytest.fixture
def sample_propositions() -> list[dict]:
    return [
        {
            "subject": "John Smith",
            "predicate": "works for",
            "object": "Acme Corp",
            "confidence": 0.95,
            "evidence": "John Smith works for Acme Corp",
        },
    ]


@pytest.fixture
def sample_state() -> dict:
    return {
        "source_metadata": {
            "document_id": "doc-001",
            "chunk_id": "chunk-001",
            "source": "test",
            "domain": "test",
        },
        "raw_text": "Acme Corp announced that John Smith was promoted to CEO.",
        "current_mention_candidates": [],
        "current_proposition_candidates": [],
        "accepted_mentions": [],
        "accepted_propositions": [],
        "latest_mention_validation": {},
        "latest_proposition_validation": {},
        "latest_repair_plan": {},
        "mention_retry_count": 0,
        "proposition_retry_count": 0,
        "max_retries": 3,
        "status": "pending",
        "audit_events": [],
        "error": "",
    }
