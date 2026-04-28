"""Behavioral tests for ExtractionClient protocol and ExtractionResult."""

from __future__ import annotations

from typing import Any

from catalyst_exgraph.protocol import ExtractionClient, ExtractionResult
from pydantic import BaseModel

# ── Protocol conformance: LLMClient ────────────────────────────────────────


def test_llm_client_satisfies_extraction_client_protocol():
    """The real LLMClient from catalyst_langgraph must satisfy ExtractionClient.

    Uses isinstance() on an instance because runtime_checkable protocols with
    non-method members (model, structured_method) don't support issubclass().
    """
    from catalyst_langgraph.clients.llm import LLMClient

    client = LLMClient(api_key="test-key", model="test-model")
    assert isinstance(client, ExtractionClient)


# ── Protocol conformance: minimal class ─────────────────────────────────────


class _MinimalClient:
    """Bare-minimum class that should satisfy ExtractionClient."""

    model: str = "test-model"
    structured_method: str = "function_calling"

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        return schema()


def test_minimal_class_satisfies_extraction_client_protocol():
    client = _MinimalClient()
    assert isinstance(client, ExtractionClient)


# ── Protocol non-conformance: missing structured_output ─────────────────────


class _IncompleteClient:
    """Class missing structured_output method -- must NOT satisfy the protocol."""

    model: str = "test-model"
    structured_method: str = "function_calling"


def test_class_missing_structured_output_does_not_satisfy_protocol():
    client = _IncompleteClient()
    assert not isinstance(client, ExtractionClient)


# ── Protocol non-conformance: missing attribute ────────────────────────────


class _MissingAttributeClient:
    """Class missing structured_method attribute."""

    model: str = "test-model"

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        return schema()


def test_class_missing_attribute_does_not_satisfy_protocol():
    client = _MissingAttributeClient()
    assert not isinstance(client, ExtractionClient)


# ── ExtractionResult dataclass ──────────────────────────────────────────────


def test_extraction_result_holds_mentions_and_assertions():
    result = ExtractionResult(
        mentions=[{"text": "Alice", "type": "PERSON"}],
        assertions=[{"subject": "Alice", "predicate": "met", "object": "Bob"}],
        stats={"duration_ms": 120},
    )
    assert len(result.mentions) == 1
    assert result.mentions[0]["text"] == "Alice"
    assert len(result.assertions) == 1
    assert result.assertions[0]["predicate"] == "met"
    assert result.stats["duration_ms"] == 120


def test_extraction_result_defaults_to_empty_collections():
    result = ExtractionResult()
    assert result.mentions == []
    assert result.assertions == []
    assert result.stats == {}
    assert result.audit_events == []
    assert result.pipeline_breakdown == {}
