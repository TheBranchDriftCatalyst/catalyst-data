"""Behavioral tests for stage graph execution (extract -> validate -> repair loop).

Tests build actual stage graphs with mock clients and verify graph execution
produces correct state transitions, accepted items, and audit trails.
"""

from __future__ import annotations

from unittest.mock import patch

from catalyst_exgraph.config import StageConfig, ner_stage_config, spo_stage_config
from catalyst_exgraph.stage import build_stage_graph
from catalyst_exgraph.state import ExGraphState

from .conftest import (
    SAMPLE_MENTIONS,
    SAMPLE_PROPOSITIONS,
    SAMPLE_TEXT,
    MockExtractionClient,
    MockMCPClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROMPT_PATCH = patch(
    "catalyst_exgraph.nodes.extract._load_prompt",
    return_value="You are an extraction assistant.",
)
_REPAIR_PROMPT_PATCH = patch(
    "catalyst_exgraph.nodes.repair._load_repair_prompt",
    return_value="Fix the extraction errors.",
)


def _make_input_state(text: str = SAMPLE_TEXT, **overrides) -> ExGraphState:
    """Build a minimal ExGraphState for stage graph invocation."""
    state: ExGraphState = {
        "raw_text": text,
        "source_metadata": {"document_id": "doc-001", "chunk_id": "chunk-001"},
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
        "max_retries": 3,
    }
    state.update(overrides)
    return state


# =====================================================================
# 1. Happy path -- NER extraction with valid validation
# =====================================================================


@_PROMPT_PATCH
async def test_ner_happy_path_valid_mentions_accepted(_prompt):
    """When extraction returns mentions and MCP says 'valid', all candidates are accepted."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="valid")
    config = ner_stage_config(max_retries=3)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    stage = result["stages"]["ner"]
    assert stage["status"] == "completed"
    assert len(stage["accepted"]) == len(SAMPLE_MENTIONS)
    # Accepted items should have IDs assigned by ValidateNode
    assert all("id" in item for item in stage["accepted"])
    assert mcp.call_count == 1


# =====================================================================
# 2. Happy path -- SPO extraction
# =====================================================================


@_PROMPT_PATCH
async def test_spo_happy_path_propositions_accepted(_prompt):
    """SPO stage: valid propositions are accepted and stage completes."""
    client = MockExtractionClient(propositions=SAMPLE_PROPOSITIONS)
    mcp = MockMCPClient(verdict="valid")
    config = spo_stage_config(max_retries=3)

    state = _make_input_state()
    state["upstream_context"] = {"accepted_mentions": SAMPLE_MENTIONS}

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(state)

    stage = result["stages"]["spo"]
    assert stage["status"] == "completed"
    assert len(stage["accepted"]) == len(SAMPLE_PROPOSITIONS)
    assert mcp.call_count == 1


# =====================================================================
# 3. Repair path -- invalid then valid on retry
# =====================================================================


@_PROMPT_PATCH
@_REPAIR_PROMPT_PATCH
async def test_repair_path_invalid_then_valid_on_retry(_repair, _prompt):
    """When MCP returns 'invalid' first, repair fires, then 'valid' on second call."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdicts=["invalid", "valid"])
    config = ner_stage_config(max_retries=3)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    stage = result["stages"]["ner"]
    assert stage["status"] == "completed"
    assert len(stage["accepted"]) > 0
    assert stage["retry_count"] == 1  # One repair cycle happened
    assert mcp.call_count == 2  # validate -> repair -> validate


# =====================================================================
# 4. Max retries exhausted -- repair loops then stops
# =====================================================================


@_PROMPT_PATCH
@_REPAIR_PROMPT_PATCH
async def test_max_retries_exhausted_ends_without_infinite_loop(_repair, _prompt):
    """When MCP always returns 'invalid', repair loops until max_retries then stops."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="invalid")
    config = ner_stage_config(max_retries=2)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    stage = result["stages"]["ner"]
    # After exhausting retries, the stage still ends (doesn't hang)
    # retry_count should equal max_retries
    assert stage["retry_count"] == 2
    # Validation calls: initial + 2 retries = 3
    assert mcp.call_count == 3


# =====================================================================
# 5. Encoder mode (max_retries=0) -- no repair, accepts valid subset
# =====================================================================


@_PROMPT_PATCH
async def test_encoder_mode_no_repair_accepts_ambiguous_subset(_prompt):
    """With max_retries=0, ambiguous verdict accepts valid subset without repair."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="ambiguous", valid_items=[0])
    config = ner_stage_config(max_retries=0)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    stage = result["stages"]["ner"]
    # Only the first item (index 0) should be accepted
    assert len(stage["accepted"]) == 1
    assert stage["accepted"][0]["text"] == SAMPLE_MENTIONS[0]["text"]
    # No repair happened
    assert stage.get("retry_count", 0) == 0
    assert mcp.call_count == 1


@_PROMPT_PATCH
async def test_encoder_mode_invalid_verdict_no_repair(_prompt):
    """With max_retries=0, even 'invalid' goes straight to END without repair."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="invalid")
    config = ner_stage_config(max_retries=0)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    stage = result["stages"]["ner"]
    # Should not have looped -- just one validate call, no repair
    assert mcp.call_count == 1
    assert stage.get("retry_count", 0) == 0


# =====================================================================
# 6. Empty extraction -- zero candidates
# =====================================================================


@_PROMPT_PATCH
async def test_empty_extraction_completes_with_empty_accepted(_prompt):
    """When extraction returns 0 candidates, stage completes with empty accepted list."""
    client = MockExtractionClient(mentions=[])
    mcp = MockMCPClient(verdict="valid")
    config = ner_stage_config(max_retries=3)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    stage = result["stages"]["ner"]
    assert stage["status"] == "completed"
    assert stage["accepted"] == []
    assert stage["candidates"] == []


# =====================================================================
# 7. Skip mode -- pass-through
# =====================================================================


async def test_skip_mode_passthrough_no_extraction():
    """When config.skip=True, no extraction or validation occurs."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="valid")
    config = ner_stage_config(max_retries=3)
    # Rebuild with skip=True (frozen dataclass)
    config = StageConfig(**{**config.__dict__, "skip": True})

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    # No stages populated -- pass-through does nothing
    assert result.get("stages", {}).get("ner") is None
    # Client and MCP never called
    assert len(client.structured_calls) == 0
    assert mcp.call_count == 0


# =====================================================================
# 8. Audit events -- verify entries have required fields
# =====================================================================


@_PROMPT_PATCH
async def test_audit_events_contain_required_fields(_prompt):
    """Audit events from extraction and validation have node_name, status, duration_s."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="valid")
    config = ner_stage_config(max_retries=3)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    events = result.get("audit_events", [])
    assert len(events) >= 2  # At least extract + validate

    for event in events:
        assert "node_name" in event
        assert "status" in event
        assert "duration_s" in event
        assert isinstance(event["duration_s"], float)
        assert event["duration_s"] >= 0


@_PROMPT_PATCH
async def test_audit_events_record_extraction_and_validation_nodes(_prompt):
    """Audit trail includes both extract_ner and validate_ner node names."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="valid")
    config = ner_stage_config(max_retries=3)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    node_names = [e["node_name"] for e in result.get("audit_events", [])]
    assert "extract_ner" in node_names
    assert "validate_ner" in node_names


# =====================================================================
# 9. Ambiguous verdict with repair -- accepts subset then repairs rest
# =====================================================================


@_PROMPT_PATCH
@_REPAIR_PROMPT_PATCH
async def test_ambiguous_verdict_accepts_subset_then_repairs(_repair, _prompt):
    """Ambiguous verdict: valid subset accepted, then repair fires for the rest."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    # First call: ambiguous (item 0 valid), second call: valid
    mcp = MockMCPClient(verdicts=["ambiguous", "valid"], valid_items=[0])
    config = ner_stage_config(max_retries=3)

    graph = build_stage_graph(config, client, mcp)
    result = await graph.ainvoke(_make_input_state())

    stage = result["stages"]["ner"]
    assert stage["status"] == "completed"
    # After repair + valid, all repaired candidates are accepted
    assert len(stage["accepted"]) > 0
    assert stage["retry_count"] == 1


# =====================================================================
# 10. Validate calls MCP with correct tool name
# =====================================================================


@_PROMPT_PATCH
async def test_validate_calls_mcp_with_correct_tool_name(_prompt):
    """ValidateNode calls mcp_client.call_tool with the config's validation_tool."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="valid")
    config = ner_stage_config(max_retries=3)

    graph = build_stage_graph(config, client, mcp)
    await graph.ainvoke(_make_input_state())

    assert len(mcp.calls) == 1
    tool_name, args = mcp.calls[0]
    assert tool_name == "validate_mentions"
    assert "mentions" in args
    assert "source_text" in args
