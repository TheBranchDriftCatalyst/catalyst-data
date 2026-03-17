"""Tests for individual node functions."""

import json

import pytest

from catalyst_langgraph.nodes.extract_mentions import make_extract_mentions
from catalyst_langgraph.nodes.extract_propositions import make_extract_propositions
from catalyst_langgraph.nodes.persist_artifacts import make_persist_artifacts
from catalyst_langgraph.nodes.repair_mentions import make_repair_mentions
from catalyst_langgraph.nodes.repair_propositions import make_repair_propositions
from catalyst_langgraph.nodes.validate_mentions import make_validate_mentions
from catalyst_langgraph.nodes.validate_propositions import make_validate_propositions
from catalyst_langgraph.repository.jsonl import JsonlRepository


@pytest.mark.asyncio
async def test_extract_mentions_node(mock_llm, sample_state, sample_mentions):
    mock_llm.set_default_mentions(sample_mentions)

    node = make_extract_mentions(mock_llm)
    result = await node(sample_state)

    assert len(result["current_mention_candidates"]) == 2
    assert result["status"] == "validating_mentions"
    assert len(result["audit_events"]) == 1


@pytest.mark.asyncio
async def test_validate_mentions_accepted(mock_mcp, sample_state, sample_mentions):
    mock_mcp.set_response(
        "validate_mentions", {"verdict": "valid", "errors": []}
    )
    sample_state["current_mention_candidates"] = sample_mentions

    node = make_validate_mentions(mock_mcp)
    result = await node(sample_state)

    assert result["accepted_mentions"] == sample_mentions
    assert result["status"] == "extracting_propositions"


@pytest.mark.asyncio
async def test_validate_mentions_rejected(mock_mcp, sample_state, sample_mentions):
    mock_mcp.set_response(
        "validate_mentions",
        {"verdict": "invalid", "errors": ["Missing entity_type"]},
    )
    sample_state["current_mention_candidates"] = sample_mentions

    node = make_validate_mentions(mock_mcp)
    result = await node(sample_state)

    assert "accepted_mentions" not in result
    assert result["status"] == "repairing_mentions"


@pytest.mark.asyncio
async def test_repair_mentions_node(mock_llm, sample_state, sample_mentions):
    repaired = [{"surface_form": "Acme Corp", "entity_type": "ORG", "start_offset": 0, "end_offset": 9}]
    mock_llm.set_default_mentions(repaired)
    sample_state["current_mention_candidates"] = sample_mentions
    sample_state["latest_mention_validation"] = {
        "verdict": "invalid",
        "errors": ["bad offset"],
    }

    node = make_repair_mentions(mock_llm)
    result = await node(sample_state)

    assert result["mention_retry_count"] == 1
    assert result["status"] == "validating_mentions"
    assert len(result["current_mention_candidates"]) == 1


@pytest.mark.asyncio
async def test_extract_propositions_node(
    mock_llm, sample_state, sample_mentions, sample_propositions
):
    mock_llm.set_default_propositions(sample_propositions)
    sample_state["accepted_mentions"] = sample_mentions

    node = make_extract_propositions(mock_llm)
    result = await node(sample_state)

    assert len(result["current_proposition_candidates"]) == 1
    assert result["status"] == "validating_propositions"


@pytest.mark.asyncio
async def test_validate_propositions_accepted(
    mock_mcp, sample_state, sample_mentions, sample_propositions
):
    mock_mcp.set_response(
        "validate_propositions", {"verdict": "valid", "errors": []}
    )
    sample_state["accepted_mentions"] = sample_mentions
    sample_state["current_proposition_candidates"] = sample_propositions

    node = make_validate_propositions(mock_mcp)
    result = await node(sample_state)

    assert result["accepted_propositions"] == sample_propositions
    assert result["status"] == "persisting"


@pytest.mark.asyncio
async def test_validate_propositions_rejected(
    mock_mcp, sample_state, sample_mentions, sample_propositions
):
    mock_mcp.set_response(
        "validate_propositions",
        {"verdict": "invalid", "errors": ["Unknown subject"]},
    )
    sample_state["accepted_mentions"] = sample_mentions
    sample_state["current_proposition_candidates"] = sample_propositions

    node = make_validate_propositions(mock_mcp)
    result = await node(sample_state)

    assert "accepted_propositions" not in result
    assert result["status"] == "repairing_propositions"


@pytest.mark.asyncio
async def test_repair_propositions_node(
    mock_llm, sample_state, sample_mentions, sample_propositions
):
    repaired = [
        {
            "subject": "John Smith",
            "predicate": "works for",
            "object": "Acme Corp",
            "confidence": 0.9,
            "evidence": "John Smith works for Acme Corp",
        }
    ]
    mock_llm.set_default_propositions(repaired)
    sample_state["accepted_mentions"] = sample_mentions
    sample_state["current_proposition_candidates"] = sample_propositions
    sample_state["latest_proposition_validation"] = {
        "verdict": "invalid",
        "errors": ["bad reference"],
    }

    node = make_repair_propositions(mock_llm)
    result = await node(sample_state)

    assert result["proposition_retry_count"] == 1
    assert result["status"] == "validating_propositions"


@pytest.mark.asyncio
async def test_persist_artifacts_node(tmp_path, sample_state, sample_mentions, sample_propositions):
    repo = JsonlRepository(tmp_path)
    sample_state["accepted_mentions"] = sample_mentions
    sample_state["accepted_propositions"] = sample_propositions

    node = make_persist_artifacts(repo)
    result = await node(sample_state)

    assert result["status"] == "completed"
    assert (tmp_path / "doc-001" / "mentions.jsonl").exists()
    assert (tmp_path / "doc-001" / "propositions.jsonl").exists()
    assert (tmp_path / "doc-001" / "audit_trail.jsonl").exists()


# --- JSON-parse error branch tests ---


class NonJsonLLM:
    """LLM mock that always returns non-JSON text."""

    async def complete(self, prompt: str, *, system: str = "") -> str:
        return "not json at all"


@pytest.mark.asyncio
async def test_extract_mentions_non_json(sample_state):
    """When the LLM returns non-JSON, extract_mentions should yield empty candidates."""
    llm = NonJsonLLM()
    node = make_extract_mentions(llm)
    result = await node(sample_state)

    assert result["current_mention_candidates"] == []
    assert result["status"] == "validating_mentions"
    assert any(
        ev["node_name"] == "extract_mentions" for ev in result["audit_events"]
    )


@pytest.mark.asyncio
async def test_extract_propositions_non_json(sample_state, sample_mentions):
    """When the LLM returns non-JSON, extract_propositions should yield empty candidates."""
    llm = NonJsonLLM()
    sample_state["accepted_mentions"] = sample_mentions
    node = make_extract_propositions(llm)
    result = await node(sample_state)

    assert result["current_proposition_candidates"] == []
    assert result["status"] == "validating_propositions"
    assert any(
        ev["node_name"] == "extract_propositions" for ev in result["audit_events"]
    )


@pytest.mark.asyncio
async def test_repair_mentions_non_json(sample_state, sample_mentions):
    """When the LLM returns non-JSON, repair_mentions should fall back to an empty list to avoid an infinite loop."""
    llm = NonJsonLLM()
    sample_state["current_mention_candidates"] = sample_mentions
    sample_state["latest_mention_validation"] = {
        "verdict": "invalid",
        "errors": ["bad offset"],
    }
    node = make_repair_mentions(llm)
    result = await node(sample_state)

    # Fallback: empty list — avoids feeding broken data back into the loop
    assert result["current_mention_candidates"] == []
    assert result["mention_retry_count"] == 1
    assert result["status"] == "validating_mentions"


@pytest.mark.asyncio
async def test_repair_propositions_non_json(
    sample_state, sample_mentions, sample_propositions
):
    """When the LLM returns non-JSON, repair_propositions should fall back to an empty list to avoid an infinite loop."""
    llm = NonJsonLLM()
    sample_state["accepted_mentions"] = sample_mentions
    sample_state["current_proposition_candidates"] = sample_propositions
    sample_state["latest_proposition_validation"] = {
        "verdict": "invalid",
        "errors": ["bad reference"],
    }
    node = make_repair_propositions(llm)
    result = await node(sample_state)

    # Fallback: empty list — avoids feeding broken data back into the loop
    assert result["current_proposition_candidates"] == []
    assert result["proposition_retry_count"] == 1
    assert result["status"] == "validating_propositions"
