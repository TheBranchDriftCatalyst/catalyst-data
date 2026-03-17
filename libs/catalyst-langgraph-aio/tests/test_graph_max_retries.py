"""Test the extraction graph — max retries path (repair exhausts retries)."""

import pytest

from catalyst_langgraph.graph import build_extraction_graph
from catalyst_langgraph.repository.jsonl import JsonlRepository


@pytest.mark.asyncio
async def test_mention_max_retries(tmp_path, mock_llm, mock_mcp, sample_mentions, sample_state):
    """Mention validation always fails, exhausts retries, graph ends with failed status."""
    mock_llm.set_default_mentions(sample_mentions)

    mock_mcp.set_response(
        "validate_mentions",
        {"verdict": "invalid", "errors": ["Always fails"]},
    )

    repo = JsonlRepository(tmp_path)
    graph = build_extraction_graph(mock_llm, mock_mcp, repo)

    state = {
        **sample_state,
        "max_retries": 2,
        "source_metadata": {**sample_state["source_metadata"], "document_id": "doc-fail"},
    }
    result = await graph.ainvoke(state)

    # Should have exhausted retries
    assert result["mention_retry_count"] >= 2
    # Should NOT have reached proposition extraction or persistence
    assert result.get("accepted_propositions", []) == []
    # Latest validation should still show rejected
    assert result["latest_mention_validation"]["verdict"] == "invalid"
    # Should NOT have persisted
    assert not (tmp_path / "doc-fail" / "mentions.jsonl").exists()


@pytest.mark.asyncio
async def test_proposition_max_retries(
    tmp_path, mock_llm, mock_mcp, sample_mentions, sample_propositions, sample_state
):
    """Proposition validation always fails, exhausts retries."""
    mock_llm.set_default_mentions(sample_mentions)
    mock_llm.set_default_propositions(sample_propositions)

    mock_mcp.set_response(
        "validate_mentions", {"verdict": "valid", "errors": []}
    )
    mock_mcp.set_response(
        "validate_propositions",
        {"verdict": "invalid", "errors": ["Always fails"]},
    )

    repo = JsonlRepository(tmp_path)
    graph = build_extraction_graph(mock_llm, mock_mcp, repo)

    state = {
        **sample_state,
        "max_retries": 2,
        "source_metadata": {**sample_state["source_metadata"], "document_id": "doc-prop-fail"},
    }
    result = await graph.ainvoke(state)

    assert result["proposition_retry_count"] >= 2
    assert result.get("accepted_propositions", []) == []
    assert result["latest_proposition_validation"]["verdict"] == "invalid"
    # Should NOT have persisted
    assert not (tmp_path / "doc-prop-fail" / "mentions.jsonl").exists()
