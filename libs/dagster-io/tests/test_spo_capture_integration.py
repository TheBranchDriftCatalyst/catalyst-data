"""Integration test for SPO prompt/response capture (Gap #5).

Drives ``catalyst_exgraph.nodes.extract.ExtractNode`` with a stub
``ExtractionClient`` so we can:

1. Confirm the SPO branch opens a thread-local capture slot.
2. Confirm the LLM client (mocked) writes raw_text + usage into the slot.
3. Confirm ``consume_spo_capture(chunk_id)`` returns the expected
   ``details`` blob shape (prompt_hash / prompt_preview / response_preview /
   usage / cost_usd / parse_errors).
4. Confirm non-SPO stages do NOT populate the capture buffer.

S3 archive writes are stubbed out so the test stays offline.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.nodes.extract import ExtractNode, consume_spo_capture
from dagster_io.bench import spo_capture


class _Proposition(BaseModel):
    subject: str = ""
    predicate: str = ""
    object: str = ""


class _PropositionResult(BaseModel):
    propositions: list[_Proposition] = []


class _StubLLMClient:
    """Mimics ``LLMClient.structured_output`` semantics enough to drive
    the capture path. Writes into the spo_capture slot exactly the way
    the real client does.
    """

    model = "gpt-4o-mini"
    structured_method = "function_calling"

    def __init__(self, *, raw_text: str, usage: dict[str, int], parsing_error: Any = None) -> None:
        self._raw_text = raw_text
        self._usage = usage
        self._parsing_error = parsing_error

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        # Real client also gates on is_capturing(); reproduce here.
        if spo_capture.is_capturing():
            spo_capture.write(self._raw_text, usage=self._usage, parsing_error=self._parsing_error)
        return schema(propositions=[_Proposition(subject="A", predicate="rel", object="B")])


@pytest.fixture
def spo_config() -> StageConfig:
    return StageConfig(
        stage_name="spo",
        extraction_schema=_PropositionResult,
        prompt_id="spo_extraction_v1",
        validation_tool="validate_propositions",
        repair_prompt_id="spo_repair_v1",
        fallback_prompt="Extract subject-predicate-object triples.",
    )


def _run_node(node: ExtractNode, state: dict) -> dict:
    return asyncio.get_event_loop().run_until_complete(node(state))


def test_spo_capture_populates_buffer(spo_config: StageConfig) -> None:
    """ExtractNode with stage_name='spo' must populate the capture buffer
    keyed on chunk_id with all six new details fields."""
    chunk_id = "doc-test:win-12345abc"
    client = _StubLLMClient(
        raw_text='{"propositions": [{"subject": "A", "predicate": "rel", "object": "B"}]}',
        usage={"tokens_in": 1000, "tokens_out": 50, "tokens_total": 1050},
    )
    node = ExtractNode(spo_config, client)

    state = {
        "raw_text": "Sample text for SPO extraction.",
        "doc_id": "doc-test",
        "chunk_id": chunk_id,
        "model": "gpt-4o-mini",
        "source_metadata": {"document_id": "doc-test", "chunk_id": chunk_id},
        "upstream_context": {"accepted_mentions": []},
        "stages": {},
        "audit_events": [],
    }

    # Patch the prompt loader to a known string, S3 archive to a no-op,
    # and the event_store path so emit_chunk_text doesn't blow up offline.
    with (
        patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="STUB SYSTEM PROMPT"),
        patch("catalyst_exgraph.nodes.extract._archive_prompt_and_response"),
        patch("catalyst_exgraph.nodes.extract.event_store.emit_chunk_text"),
        patch("catalyst_exgraph.nodes.extract.event_store.current_run_id", return_value="test-run-id"),
    ):
        _run_node(node, state)

    # Buffer must have been populated; consume + verify.
    cap = consume_spo_capture(chunk_id)
    assert cap is not None, "SPO capture buffer was not populated"

    # Required fields per the gap spec
    assert "prompt_hash" in cap and len(cap["prompt_hash"]) == 16
    assert cap["prompt_preview"].startswith("STUB SYSTEM PROMPT")
    assert "Sample text for SPO extraction" in cap["prompt_preview"]
    assert "propositions" in cap["response_preview"]
    assert cap["usage"] == {"tokens_in": 1000, "tokens_out": 50, "tokens_total": 1050}
    # gpt-4o-mini at 1000 input + 50 output tokens
    expected_cost = (1000 * 0.15 + 50 * 0.60) / 1_000_000
    assert cap["cost_usd"] == pytest.approx(expected_cost)
    assert cap["parse_errors"] == []

    # Idempotent consume — second call returns None (we popped).
    assert consume_spo_capture(chunk_id) is None


def test_spo_capture_records_parse_errors_on_empty(spo_config: StageConfig) -> None:
    """When the LLM returns raw text but the parsed schema yields zero
    candidates, parse_errors must include an ``empty`` stage entry."""
    chunk_id = "doc-empty:win-deadbeef"

    class _EmptyClient:
        model = "gemma3-12b"
        structured_method = "json_mode"

        async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
            if spo_capture.is_capturing():
                spo_capture.write(
                    "nonsense raw output that didn't validate",
                    usage={"tokens_in": 50, "tokens_out": 5, "tokens_total": 55},
                )
            return schema(propositions=[])

    node = ExtractNode(spo_config, _EmptyClient())
    state = {
        "raw_text": "x",
        "doc_id": "doc-empty",
        "chunk_id": chunk_id,
        "model": "gemma3-12b",
        "source_metadata": {"document_id": "doc-empty", "chunk_id": chunk_id},
        "upstream_context": {"accepted_mentions": []},
        "stages": {},
        "audit_events": [],
    }

    with (
        patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="SYS"),
        patch("catalyst_exgraph.nodes.extract._archive_prompt_and_response"),
        patch("catalyst_exgraph.nodes.extract.event_store.emit_chunk_text"),
        patch("catalyst_exgraph.nodes.extract.event_store.current_run_id", return_value="r"),
    ):
        _run_node(node, state)

    cap = consume_spo_capture(chunk_id)
    assert cap is not None
    assert any(e["stage"] == "empty" for e in cap["parse_errors"])
    # gemma3-12b is in rate table at $0/Mtok
    assert cap["cost_usd"] == 0.0


def test_non_spo_stage_does_not_populate_buffer() -> None:
    """A non-SPO stage (stage_name='ner_repair' or similar) must NOT
    write to the capture buffer; the ExtractNode skips the capture
    path entirely."""
    config = StageConfig(
        stage_name="ner_repair",
        extraction_schema=_PropositionResult,
        prompt_id="ner_repair_v1",
        validation_tool="validate_mentions",
        repair_prompt_id="ner_repair_v1",
        fallback_prompt="Repair the entity list.",
    )

    class _Client:
        model = "gpt-4o-mini"
        structured_method = "function_calling"

        async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
            # Even if the slot was somehow open, this stage's chunk_id should
            # not get bookkeeped into the SPO buffer.
            return schema(propositions=[])

    chunk_id = "doc-nonspo:chunk-1"
    node = ExtractNode(config, _Client())
    state = {
        "raw_text": "x",
        "doc_id": "doc-nonspo",
        "chunk_id": chunk_id,
        "model": "gpt-4o-mini",
        "source_metadata": {"document_id": "doc-nonspo", "chunk_id": chunk_id},
        "stages": {},
        "audit_events": [],
    }

    with (
        patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="SYS"),
        patch("catalyst_exgraph.nodes.extract.event_store.emit_chunk_text"),
    ):
        _run_node(node, state)

    assert consume_spo_capture(chunk_id) is None
