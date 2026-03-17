"""Test the extraction graph — repair path (first validation fails, repair succeeds)."""

import pytest

from catalyst_langgraph.graph import build_extraction_graph
from catalyst_langgraph.repository.jsonl import JsonlRepository


@pytest.mark.asyncio
async def test_mention_repair_then_success(
    tmp_path, mock_llm, mock_mcp, sample_mentions, sample_propositions, sample_state
):
    """Mention validation fails on first try, repair fixes it, then succeeds."""
    mock_llm.set_default_mentions(sample_mentions)
    mock_llm.set_default_propositions(sample_propositions)

    call_count = {"validate_mentions": 0}

    def mention_validator(args):
        call_count["validate_mentions"] += 1
        if call_count["validate_mentions"] == 1:
            return {"verdict": "invalid", "errors": ["Missing start_offset"]}
        return {"verdict": "valid", "errors": []}

    mock_mcp.set_response("validate_mentions", mention_validator)
    mock_mcp.set_response(
        "validate_propositions", {"verdict": "valid", "errors": []}
    )

    repo = JsonlRepository(tmp_path)
    graph = build_extraction_graph(mock_llm, mock_mcp, repo)

    state = {**sample_state, "source_metadata": {**sample_state["source_metadata"], "document_id": "doc-repair"}}
    result = await graph.ainvoke(state)

    assert result["status"] == "completed"
    assert result["mention_retry_count"] == 1
    assert len(result["accepted_mentions"]) > 0


@pytest.mark.asyncio
async def test_proposition_repair_then_success(
    tmp_path, mock_llm, mock_mcp, sample_mentions, sample_propositions, sample_state
):
    """Proposition validation fails on first try, repair fixes it, then succeeds."""
    mock_llm.set_default_mentions(sample_mentions)
    mock_llm.set_default_propositions(sample_propositions)

    mock_mcp.set_response(
        "validate_mentions", {"verdict": "valid", "errors": []}
    )

    prop_call_count = {"n": 0}

    def proposition_validator(args):
        prop_call_count["n"] += 1
        if prop_call_count["n"] == 1:
            return {"verdict": "invalid", "errors": ["Unknown predicate"]}
        return {"verdict": "valid", "errors": []}

    mock_mcp.set_response("validate_propositions", proposition_validator)

    repo = JsonlRepository(tmp_path)
    graph = build_extraction_graph(mock_llm, mock_mcp, repo)

    state = {**sample_state, "source_metadata": {**sample_state["source_metadata"], "document_id": "doc-prop-repair"}}
    result = await graph.ainvoke(state)

    assert result["status"] == "completed"
    assert result["proposition_retry_count"] == 1
