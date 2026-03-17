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
    # Must be marked as failed (A2 fix)
    assert result["status"] == "failed"
    # Must have a failure audit event
    failure_events = [e for e in result["audit_events"] if e["node_name"] == "failure_handler"]
    assert len(failure_events) == 1
    assert failure_events[0]["details"]["reason"] == "max retries exhausted"


@pytest.mark.asyncio
async def test_mention_max_retries_zero(tmp_path, mock_llm, mock_mcp, sample_mentions, sample_state):
    """With max_retries=0, first validation failure should immediately fail (no repair attempts)."""
    mock_llm.set_default_mentions(sample_mentions)
    mock_mcp.set_response(
        "validate_mentions",
        {"verdict": "invalid", "errors": ["Instant fail"]},
    )

    repo = JsonlRepository(tmp_path)
    graph = build_extraction_graph(mock_llm, mock_mcp, repo)

    state = {
        **sample_state,
        "max_retries": 0,
        "source_metadata": {**sample_state["source_metadata"], "document_id": "doc-zero"},
    }
    result = await graph.ainvoke(state)

    # Should fail immediately without any repair attempts
    assert result["mention_retry_count"] == 0
    assert result["status"] == "failed"
    # Verify the exact enum value is used (not a bare string)
    from catalyst_langgraph.state import WorkflowStatus
    assert result["status"] == WorkflowStatus.FAILED.value
    assert not (tmp_path / "doc-zero" / "mentions.jsonl").exists()


@pytest.mark.asyncio
async def test_mention_max_retries_one(tmp_path, mock_llm, mock_mcp, sample_mentions, sample_state):
    """With max_retries=1, should attempt one repair then fail on second validation."""
    mock_llm.set_default_mentions(sample_mentions)
    mock_mcp.set_response(
        "validate_mentions",
        {"verdict": "invalid", "errors": ["Still bad"]},
    )

    repo = JsonlRepository(tmp_path)
    graph = build_extraction_graph(mock_llm, mock_mcp, repo)

    state = {
        **sample_state,
        "max_retries": 1,
        "source_metadata": {**sample_state["source_metadata"], "document_id": "doc-one"},
    }
    result = await graph.ainvoke(state)

    assert result["mention_retry_count"] == 1
    assert result["status"] == "failed"
    # Audit events should include nodes from the attempted repair cycle
    node_names = [e["node_name"] for e in result["audit_events"]]
    assert "extract_mentions" in node_names
    assert "validate_mentions" in node_names
    assert "repair_mentions" in node_names
    assert "failure_handler" in node_names


@pytest.mark.asyncio
async def test_failure_status_uses_enum_value(tmp_path, mock_llm, mock_mcp, sample_mentions, sample_state):
    """Verify the status is exactly WorkflowStatus.FAILED.value, not a bare string."""
    from catalyst_langgraph.state import WorkflowStatus

    mock_llm.set_default_mentions(sample_mentions)
    mock_mcp.set_response(
        "validate_mentions",
        {"verdict": "invalid", "errors": ["Fail"]},
    )

    repo = JsonlRepository(tmp_path)
    graph = build_extraction_graph(mock_llm, mock_mcp, repo)

    state = {
        **sample_state,
        "max_retries": 1,
        "source_metadata": {**sample_state["source_metadata"], "document_id": "doc-enum"},
    }
    result = await graph.ainvoke(state)

    assert result["status"] == WorkflowStatus.FAILED.value
    assert result["status"] == "failed"


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
    # Must be marked as failed (A2 fix)
    assert result["status"] == "failed"
    # Must have a failure audit event
    failure_events = [e for e in result["audit_events"] if e["node_name"] == "failure_handler"]
    assert len(failure_events) == 1
    assert failure_events[0]["details"]["reason"] == "max retries exhausted"
