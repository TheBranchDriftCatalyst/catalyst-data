"""Full-flow integration test: LLM extraction → MCP contract validation → repair → persistence.

This test exercises the complete trust boundary pipeline using MockMCPClient.
The LLM is mocked (no network calls), but all graph orchestration, routing,
repair loops, and persistence run against real code.

Run:
    uv run pytest tests/test_full_flow_integration.py -v
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from catalyst_langgraph.clients.mcp import MockMCPClient
from catalyst_langgraph.graph import build_extraction_graph
from catalyst_langgraph.repository.jsonl import JsonlRepository


SOURCE_TEXT = (
    "The United Nations was founded in 1945 by 51 countries committed to "
    "maintaining international peace and security. Its headquarters is "
    "located in New York City, United States."
)


class MockLLMForIntegration:
    """Mock LLM that returns realistic mention and proposition data."""

    def __init__(self, mentions: list[dict], propositions: list[dict]):
        self._mentions = mentions
        self._propositions = propositions

    async def complete(self, prompt: str, *, system: str = "") -> str:
        if "proposition" in system.lower() or "triple" in system.lower():
            return json.dumps({"propositions": self._propositions})
        return json.dumps({"mentions": self._mentions})

    async def structured_output(self, schema, messages) -> Any:
        return None


# --- Fixtures ---


@pytest.fixture
def valid_mentions() -> list[dict]:
    """Mentions with spans that actually align with SOURCE_TEXT."""
    return [
        {
            "surface_form": "United Nations",
            "entity_type": "ORG",
            "start_offset": 4,
            "end_offset": 18,
            "text": "United Nations",
            "mention_type": "ORG",
            "span_start": 4,
            "span_end": 18,
            "confidence": 0.95,
        },
        {
            "surface_form": "New York City",
            "entity_type": "GPE",
            "start_offset": 145,
            "end_offset": 158,
            "text": "New York City",
            "mention_type": "GPE",
            "span_start": 145,
            "span_end": 158,
            "confidence": 0.92,
        },
    ]


@pytest.fixture
def invalid_mentions_then_fixed() -> tuple[list[dict], list[dict]]:
    """First attempt has wrong spans; repaired version has correct spans."""
    bad = [
        {
            "surface_form": "United Nations",
            "entity_type": "ORG",
            "text": "United Nations",
            "mention_type": "ORG",
            "span_start": 0,  # Wrong! Should be 4
            "span_end": 14,   # Wrong! Should be 18
            "confidence": 0.95,
        },
    ]
    good = [
        {
            "surface_form": "United Nations",
            "entity_type": "ORG",
            "text": "United Nations",
            "mention_type": "ORG",
            "span_start": 4,
            "span_end": 18,
            "confidence": 0.95,
        },
    ]
    return bad, good


@pytest.fixture
def valid_propositions() -> list[dict]:
    return [
        {
            "subject": "United Nations",
            "predicate": "founded_in",
            "object": "1945",
            "confidence": 0.9,
        },
    ]


def _make_state(document_id: str = "doc-e2e", max_retries: int = 3) -> dict:
    return {
        "source_metadata": {
            "document_id": document_id,
            "chunk_id": "chunk-001",
            "source": "test",
            "domain": "test",
        },
        "raw_text": SOURCE_TEXT,
        "current_mention_candidates": [],
        "current_proposition_candidates": [],
        "accepted_mentions": [],
        "accepted_propositions": [],
        "latest_mention_validation": {},
        "latest_proposition_validation": {},
        "latest_repair_plan": {},
        "mention_retry_count": 0,
        "proposition_retry_count": 0,
        "max_retries": max_retries,
        "status": "pending",
        "audit_events": [],
        "error": "",
    }


# --- Tests ---


class TestFullFlowEndToEnd:
    """Full graph flow using MockMCPClient — exercises all paths."""

    @pytest.mark.asyncio
    async def test_happy_path_end_to_end(
        self, tmp_path, valid_mentions, valid_propositions
    ):
        """LLM extracts → MCP validates (mock accepts) → persisted."""
        llm = MockLLMForIntegration(valid_mentions, valid_propositions)
        mcp = MockMCPClient(
            {
                "validate_mentions": {"verdict": "valid", "errors": []},
                "validate_propositions": {"verdict": "valid", "errors": []},
            }
        )
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(_make_state("doc-e2e"))

        assert result["status"] == "completed"
        assert len(result["accepted_mentions"]) == 2
        assert len(result["accepted_propositions"]) == 1

        # Verify files persisted
        assert (tmp_path / "doc-e2e" / "mentions.jsonl").exists()
        assert (tmp_path / "doc-e2e" / "propositions.jsonl").exists()
        assert (tmp_path / "doc-e2e" / "audit_trail.jsonl").exists()

        # Verify audit trail file (written before persist event is appended to state)
        audit_lines = (tmp_path / "doc-e2e" / "audit_trail.jsonl").read_text().strip().split("\n")
        file_events = [json.loads(line) for line in audit_lines]
        file_node_names = [e["node_name"] for e in file_events]
        assert "extract_mentions" in file_node_names
        assert "validate_mentions" in file_node_names
        assert "extract_propositions" in file_node_names
        assert "validate_propositions" in file_node_names

        # persist_artifacts event is added to state after file write
        state_node_names = [e["node_name"] for e in result["audit_events"]]
        assert "persist_artifacts" in state_node_names

    @pytest.mark.asyncio
    async def test_repair_loop_end_to_end(
        self, tmp_path, invalid_mentions_then_fixed, valid_propositions
    ):
        """LLM extracts bad spans → MCP rejects → LLM repairs → MCP accepts → persisted."""
        bad, good = invalid_mentions_then_fixed
        call_count = {"n": 0}

        class RepairingLLM:
            async def complete(self, prompt: str, *, system: str = "") -> str:
                if "proposition" in system.lower() or "triple" in system.lower():
                    return json.dumps({"propositions": valid_propositions})
                call_count["n"] += 1
                if call_count["n"] <= 1:
                    return json.dumps({"mentions": bad})
                return json.dumps({"mentions": good})

            async def structured_output(self, schema, messages):
                return None

        mention_validate_count = {"n": 0}

        def validate_switch(args):
            mention_validate_count["n"] += 1
            if mention_validate_count["n"] <= 1:
                return {"verdict": "invalid", "errors": ["Span mismatch"]}
            return {"verdict": "valid", "errors": []}

        mcp = MockMCPClient()
        mcp.set_response("validate_mentions", validate_switch)
        mcp.set_response("validate_propositions", {"verdict": "valid", "errors": []})

        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(RepairingLLM(), mcp, repo)

        result = await graph.ainvoke(_make_state("doc-repair-e2e"))

        assert result["status"] == "completed"
        assert result["mention_retry_count"] == 1  # repaired once
        assert len(result["accepted_mentions"]) > 0
        assert (tmp_path / "doc-repair-e2e" / "mentions.jsonl").exists()

    @pytest.mark.asyncio
    async def test_max_retries_graceful_failure(self, tmp_path, valid_propositions):
        """LLM always produces bad data → MCP always rejects → graph fails gracefully."""
        bad_mentions = [{"text": "WRONG", "mention_type": "BOGUS", "span_start": 0, "span_end": 5, "confidence": 0.9}]
        llm = MockLLMForIntegration(bad_mentions, valid_propositions)
        mcp = MockMCPClient(
            {"validate_mentions": {"verdict": "invalid", "errors": ["Invalid type"]}}
        )
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(_make_state("doc-fail-e2e", max_retries=2))

        assert result["mention_retry_count"] >= 2
        assert result["latest_mention_validation"]["verdict"] == "invalid"
        # Nothing persisted
        assert not (tmp_path / "doc-fail-e2e" / "mentions.jsonl").exists()
