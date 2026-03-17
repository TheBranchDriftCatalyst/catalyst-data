"""Test the full extraction graph — happy path (all validations pass)."""

import pytest

from catalyst_langgraph.graph import build_extraction_graph
from catalyst_langgraph.repository.jsonl import JsonlRepository


@pytest.mark.asyncio
async def test_happy_path(tmp_path, mock_llm, mock_mcp, sample_mentions, sample_propositions, sample_state):
    """All validations pass on first try. Graph goes:
    extract_mentions -> validate_mentions -> extract_propositions
    -> validate_propositions -> persist_artifacts -> END
    """
    mock_llm.set_default_mentions(sample_mentions)
    mock_llm.set_default_propositions(sample_propositions)

    mock_mcp.set_response(
        "validate_mentions", {"verdict": "valid", "errors": []}
    )
    mock_mcp.set_response(
        "validate_propositions", {"verdict": "valid", "errors": []}
    )

    repo = JsonlRepository(tmp_path)
    graph = build_extraction_graph(mock_llm, mock_mcp, repo)

    result = await graph.ainvoke(sample_state)

    assert result["status"] == "completed"
    assert len(result["accepted_mentions"]) == 2
    assert len(result["accepted_propositions"]) == 1

    # Verify persistence
    assert (tmp_path / "doc-001" / "mentions.jsonl").exists()
    assert (tmp_path / "doc-001" / "propositions.jsonl").exists()
    assert (tmp_path / "doc-001" / "audit_trail.jsonl").exists()

    # Verify MCP was called for both validations
    tool_names = [call[0] for call in mock_mcp.calls]
    assert "validate_mentions" in tool_names
    assert "validate_propositions" in tool_names
