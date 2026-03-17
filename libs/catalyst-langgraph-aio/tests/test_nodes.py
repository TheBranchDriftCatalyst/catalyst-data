"""Tests for individual node functions."""

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
    repaired = [{"text": "Acme Corp", "mention_type": "ORG", "span_start": 0, "span_end": 9, "confidence": 1.0}]
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


# --- LLM error branch tests ---
# With structured_output(), JSON parse errors are replaced by exceptions from
# the LLM client (e.g., Pydantic validation errors, API errors).  The nodes'
# exception handlers catch these and set status to "failed".


class FailingLLM:
    """LLM mock that raises an exception from structured_output()."""

    async def complete(self, prompt: str, *, system: str = "") -> str:
        return "not json at all"

    async def structured_output(self, schema, messages):
        raise ValueError("LLM schema validation failed")


@pytest.mark.asyncio
async def test_extract_mentions_llm_error(sample_state):
    """When the LLM raises an exception, extract_mentions should set status to failed."""
    llm = FailingLLM()
    node = make_extract_mentions(llm)
    result = await node(sample_state)

    assert result["status"] == "failed"
    assert "LLM schema validation failed" in result["error"]
    assert any(
        ev["node_name"] == "extract_mentions" and ev["status"] == "error"
        for ev in result["audit_events"]
    )


@pytest.mark.asyncio
async def test_extract_propositions_llm_error(sample_state, sample_mentions):
    """When the LLM raises an exception, extract_propositions should set status to failed."""
    llm = FailingLLM()
    sample_state["accepted_mentions"] = sample_mentions
    node = make_extract_propositions(llm)
    result = await node(sample_state)

    assert result["status"] == "failed"
    assert "LLM schema validation failed" in result["error"]
    assert any(
        ev["node_name"] == "extract_propositions" and ev["status"] == "error"
        for ev in result["audit_events"]
    )


@pytest.mark.asyncio
async def test_repair_mentions_llm_error(sample_state, sample_mentions):
    """When the LLM raises an exception, repair_mentions should set status to failed."""
    llm = FailingLLM()
    sample_state["current_mention_candidates"] = sample_mentions
    sample_state["latest_mention_validation"] = {
        "verdict": "invalid",
        "errors": ["bad offset"],
    }
    node = make_repair_mentions(llm)
    result = await node(sample_state)

    assert result["status"] == "failed"
    assert "LLM schema validation failed" in result["error"]
    assert any(
        ev["node_name"] == "repair_mentions" and ev["status"] == "error"
        for ev in result["audit_events"]
    )


@pytest.mark.asyncio
async def test_repair_propositions_llm_error(
    sample_state, sample_mentions, sample_propositions
):
    """When the LLM raises an exception, repair_propositions should set status to failed."""
    llm = FailingLLM()
    sample_state["accepted_mentions"] = sample_mentions
    sample_state["current_proposition_candidates"] = sample_propositions
    sample_state["latest_proposition_validation"] = {
        "verdict": "invalid",
        "errors": ["bad reference"],
    }
    node = make_repair_propositions(llm)
    result = await node(sample_state)

    assert result["status"] == "failed"
    assert "LLM schema validation failed" in result["error"]
    assert any(
        ev["node_name"] == "repair_propositions" and ev["status"] == "error"
        for ev in result["audit_events"]
    )


# --- A3 edge-case tests: composite ID generation in validate_mentions ---


@pytest.mark.asyncio
async def test_validate_mentions_assigns_composite_ids(mock_mcp, sample_state):
    """Accepted mentions get span-based composite IDs assigned."""
    mock_mcp.set_response("validate_mentions", {"verdict": "valid", "errors": []})
    mentions = [
        {
            "surface_form": "Acme Corp",
            "mention_type": "ORG",
            "span_start": 0,
            "span_end": 9,
        },
        {
            "surface_form": "John Smith",
            "mention_type": "PERSON",
            "span_start": 20,
            "span_end": 30,
        },
    ]
    sample_state["current_mention_candidates"] = mentions

    node = make_validate_mentions(mock_mcp)
    result = await node(sample_state)

    assert result["accepted_mentions"][0]["id"] == "ORG:0:9"
    assert result["accepted_mentions"][1]["id"] == "PERSON:20:30"


@pytest.mark.asyncio
async def test_validate_mentions_id_deterministic(mock_mcp, sample_state):
    """Same mention always produces the same composite ID."""
    mock_mcp.set_response("validate_mentions", {"verdict": "valid", "errors": []})
    mention = {
        "surface_form": "Acme Corp",
        "mention_type": "ORG",
        "span_start": 5,
        "span_end": 14,
    }
    sample_state["current_mention_candidates"] = [mention.copy()]

    node = make_validate_mentions(mock_mcp)
    result1 = await node(sample_state)

    sample_state["current_mention_candidates"] = [mention.copy()]
    result2 = await node(sample_state)

    assert result1["accepted_mentions"][0]["id"] == result2["accepted_mentions"][0]["id"]
    assert result1["accepted_mentions"][0]["id"] == "ORG:5:14"


@pytest.mark.asyncio
async def test_validate_mentions_zero_span(mock_mcp, sample_state):
    """Mention with span_start == span_end still gets an ID (degenerate span)."""
    mock_mcp.set_response("validate_mentions", {"verdict": "valid", "errors": []})
    mentions = [
        {
            "surface_form": "",
            "mention_type": "ORG",
            "span_start": 10,
            "span_end": 10,
        }
    ]
    sample_state["current_mention_candidates"] = mentions

    node = make_validate_mentions(mock_mcp)
    result = await node(sample_state)

    assert result["accepted_mentions"][0]["id"] == "ORG:10:10"


@pytest.mark.asyncio
async def test_validate_mentions_overlapping_spans_unique_ids(mock_mcp, sample_state):
    """Multiple mentions with same type but different spans get unique IDs."""
    mock_mcp.set_response("validate_mentions", {"verdict": "valid", "errors": []})
    mentions = [
        {"surface_form": "Acme", "mention_type": "ORG", "span_start": 0, "span_end": 4},
        {"surface_form": "Acme Corp", "mention_type": "ORG", "span_start": 0, "span_end": 9},
    ]
    sample_state["current_mention_candidates"] = mentions

    node = make_validate_mentions(mock_mcp)
    result = await node(sample_state)

    ids = [m["id"] for m in result["accepted_mentions"]]
    assert ids[0] == "ORG:0:4"
    assert ids[1] == "ORG:0:9"
    assert len(set(ids)) == 2  # unique


@pytest.mark.asyncio
async def test_validate_mentions_uses_entity_type_fallback(mock_mcp, sample_state):
    """ID generation falls back to entity_type when mention_type is absent."""
    mock_mcp.set_response("validate_mentions", {"verdict": "valid", "errors": []})
    mentions = [
        {
            "surface_form": "Acme Corp",
            "entity_type": "ORG",
            "start_offset": 0,
            "end_offset": 9,
        }
    ]
    sample_state["current_mention_candidates"] = mentions

    node = make_validate_mentions(mock_mcp)
    result = await node(sample_state)

    assert result["accepted_mentions"][0]["id"] == "ORG:0:9"


@pytest.mark.asyncio
async def test_validate_mentions_missing_all_fields_uses_defaults(mock_mcp, sample_state):
    """ID generation uses UNK:0:0 when mention_type and span fields are absent."""
    mock_mcp.set_response("validate_mentions", {"verdict": "valid", "errors": []})
    mentions = [{"surface_form": "something"}]
    sample_state["current_mention_candidates"] = mentions

    node = make_validate_mentions(mock_mcp)
    result = await node(sample_state)

    assert result["accepted_mentions"][0]["id"] == "UNK:0:0"


@pytest.mark.asyncio
async def test_validate_propositions_empty_accepted_mentions(mock_mcp, sample_state, sample_propositions):
    """When accepted_mentions is empty, known_mention_ids should be empty."""
    sample_state["accepted_mentions"] = []
    sample_state["current_proposition_candidates"] = sample_propositions
    mock_mcp.set_response("validate_propositions", {"verdict": "valid", "errors": []})

    node = make_validate_propositions(mock_mcp)
    await node(sample_state)

    # Check MCP was called with empty known_mention_ids
    call_args = mock_mcp.calls[-1]
    assert call_args[0] == "validate_propositions"
    assert call_args[1]["known_mention_ids"] == []


@pytest.mark.asyncio
async def test_validate_propositions_passes_composite_ids(mock_mcp, sample_state, sample_propositions):
    """validate_propositions sends composite IDs (not surface forms) as known_mention_ids."""
    sample_state["accepted_mentions"] = [
        {"id": "ORG:0:9", "surface_form": "Acme Corp", "mention_type": "ORG"},
        {"id": "PERSON:20:30", "surface_form": "John Smith", "mention_type": "PERSON"},
    ]
    sample_state["current_proposition_candidates"] = sample_propositions
    mock_mcp.set_response("validate_propositions", {"verdict": "valid", "errors": []})

    node = make_validate_propositions(mock_mcp)
    await node(sample_state)

    call_args = mock_mcp.calls[-1]
    known_ids = call_args[1]["known_mention_ids"]
    assert "ORG:0:9" in known_ids
    assert "PERSON:20:30" in known_ids
    # Surface forms must NOT appear
    assert "Acme Corp" not in known_ids
    assert "John Smith" not in known_ids


@pytest.mark.asyncio
async def test_validate_propositions_skips_mentions_without_id(mock_mcp, sample_state, sample_propositions):
    """Mentions without an 'id' field are excluded from known_mention_ids."""
    sample_state["accepted_mentions"] = [
        {"id": "ORG:0:9", "surface_form": "Acme Corp"},
        {"surface_form": "John Smith"},  # no id field
    ]
    sample_state["current_proposition_candidates"] = sample_propositions
    mock_mcp.set_response("validate_propositions", {"verdict": "valid", "errors": []})

    node = make_validate_propositions(mock_mcp)
    await node(sample_state)

    call_args = mock_mcp.calls[-1]
    known_ids = call_args[1]["known_mention_ids"]
    assert known_ids == ["ORG:0:9"]
