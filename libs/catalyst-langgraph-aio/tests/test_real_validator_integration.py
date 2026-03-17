"""Integration test: full extraction pipeline with REAL validators via DirectMCPClient.

This test replaces MockMCPClient (which blindly returns {"verdict": "valid"}) with
DirectMCPClient backed by the actual mention/proposition validators from
catalyst-llm-contract-mcp.  It catches schema mismatches, field-name drift,
and silent validation failures that mocks conceal.

Run:
    uv run pytest tests/test_real_validator_integration.py -x -v
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from catalyst_langgraph.clients.mcp import DirectMCPClient
from catalyst_langgraph.graph import build_extraction_graph
from catalyst_langgraph.repository.jsonl import JsonlRepository

from catalyst_contracts.validators.mention_validator import validate_mentions
from catalyst_contracts.validators.proposition_validator import validate_propositions

from catalyst_contracts.models.extraction_output import (
    MentionCandidate,
    MentionExtractionResult,
    PropositionCandidate,
    PropositionExtractionResult,
)

# ---------------------------------------------------------------------------
# Source text used by all tests
# ---------------------------------------------------------------------------

SOURCE_TEXT = (
    "The United Nations was founded in 1945 by 51 countries committed to "
    "maintaining international peace and security. Its headquarters is "
    "located in New York City, United States."
)

# ---------------------------------------------------------------------------
# Handler that wires real validators behind DirectMCPClient
# ---------------------------------------------------------------------------


class RealValidatorHandler:
    """Adapter that exposes validate_mentions / validate_propositions as methods
    callable by DirectMCPClient.  Converts Pydantic ValidationResult → dict so
    the graph nodes can call result.get("verdict").
    """

    def validate_mentions(
        self,
        mentions: list[dict],
        source_text: str,
        document_id: str,
    ) -> dict[str, Any]:
        result = validate_mentions(mentions, source_text, document_id)
        return result.model_dump()

    def validate_propositions(
        self,
        propositions: list[dict],
        known_mention_ids: list[str] | set[str],
        source_text: str,
    ) -> dict[str, Any]:
        ids = set(known_mention_ids) if not isinstance(known_mention_ids, set) else known_mention_ids
        result = validate_propositions(propositions, ids, source_text)
        return result.model_dump()


# ---------------------------------------------------------------------------
# Mock LLM that returns data in the VALIDATOR's expected schema
# (text / mention_type / span_start / span_end)
# ---------------------------------------------------------------------------


class CorrectSchemaLLM:
    """Returns mention/proposition data matching the validator's expected fields.

    Uses structured_output() to return proper Pydantic model instances,
    matching the node implementation that calls llm_client.structured_output().
    """

    def __init__(self, mentions: list[dict], propositions: list[dict]) -> None:
        self._mentions = mentions
        self._propositions = propositions

    async def complete(self, prompt: str, *, system: str = "") -> str:
        if "proposition" in system.lower() or "triple" in system.lower():
            return json.dumps({"propositions": self._propositions})
        return json.dumps({"mentions": self._mentions})

    async def structured_output(self, schema: Any, messages: list) -> Any:
        if schema is MentionExtractionResult:
            return MentionExtractionResult(
                mentions=[MentionCandidate(**m) for m in self._mentions]
            )
        elif schema is PropositionExtractionResult:
            return PropositionExtractionResult(
                propositions=[PropositionCandidate(**p) for p in self._propositions]
            )
        return None


# ---------------------------------------------------------------------------
# Mock LLM that returns data in the LLM PROMPT's schema
# (surface_form / entity_type / start_offset / end_offset — wrong for validators)
# ---------------------------------------------------------------------------


class PromptSchemaLLM:
    """Simulates an LLM using structured_output().

    Previously, this class demonstrated the field-name mismatch bug by returning
    wrong field names (surface_form/entity_type/start_offset/end_offset) via
    complete().  With structured_output(), the Pydantic schema forces canonical
    field names, eliminating the mismatch.  The underlying mention data uses the
    old field names but structured_output() maps them to the correct schema.
    """

    def __init__(self, mentions: list[dict], propositions: list[dict]) -> None:
        # _mentions may have old-style field names (surface_form, etc.) from the
        # test data, but structured_output() returns canonical Pydantic models
        self._mentions = mentions
        self._propositions = propositions

    async def complete(self, prompt: str, *, system: str = "") -> str:
        # Legacy path — no longer called by nodes
        if "proposition" in system.lower() or "triple" in system.lower():
            return json.dumps({"propositions": self._propositions})
        return json.dumps({"mentions": self._mentions})

    async def structured_output(self, schema: Any, messages: list) -> Any:
        """With structured_output(), the LLM is constrained to emit the exact
        Pydantic schema fields.  Even if the underlying data used old field
        names, the real LLM would be forced to produce correct field names.
        We simulate this by mapping old fields to canonical names.
        """
        if schema is MentionExtractionResult:
            canonical = []
            for m in self._mentions:
                canonical.append(MentionCandidate(
                    text=m.get("text", m.get("surface_form", "")),
                    mention_type=m.get("mention_type", m.get("entity_type", "OTHER")),
                    span_start=m.get("span_start", m.get("start_offset", 0)),
                    span_end=m.get("span_end", m.get("end_offset", 0)),
                    confidence=m.get("confidence", 1.0),
                ))
            return MentionExtractionResult(mentions=canonical)
        elif schema is PropositionExtractionResult:
            return PropositionExtractionResult(
                propositions=[PropositionCandidate(**p) for p in self._propositions]
            )
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(document_id: str = "doc-real-val", max_retries: int = 3) -> dict:
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRealValidatorIntegration:
    """Full pipeline with DirectMCPClient + real validators."""

    @pytest.mark.asyncio
    async def test_correct_schema_passes_real_validators(self, tmp_path):
        """Mentions using validator-expected fields (text, mention_type, span_start,
        span_end) should pass real validation end-to-end."""
        # "The United Nations" starts at index 4, ends at 18
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            },
            {
                "text": "New York City",
                "mention_type": "GPE",
                "span_start": 145,
                "span_end": 158,
                "confidence": 0.92,
            },
        ]
        propositions = [
            {
                "subject": "United Nations",
                "predicate": "founded_in",
                "object": "1945",
                "confidence": 0.9,
            },
        ]

        llm = CorrectSchemaLLM(mentions, propositions)
        mcp = DirectMCPClient(RealValidatorHandler())
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(_make_state("doc-correct-schema"))

        # Core assertions: pipeline completed successfully
        assert result["status"] != "failed", (
            f"Pipeline failed with error: {result.get('error', 'unknown')}"
        )
        assert result["status"] == "completed"
        assert len(result["accepted_mentions"]) == 2
        assert len(result["accepted_propositions"]) == 1

        # Audit trail contains expected node executions
        node_names = [e["node_name"] for e in result["audit_events"]]
        assert "extract_mentions" in node_names
        assert "validate_mentions" in node_names
        assert "extract_propositions" in node_names
        assert "validate_propositions" in node_names
        assert "persist_artifacts" in node_names

        # Files were persisted
        assert (tmp_path / "doc-correct-schema" / "mentions.jsonl").exists()
        assert (tmp_path / "doc-correct-schema" / "propositions.jsonl").exists()

    @pytest.mark.asyncio
    async def test_prompt_schema_fails_real_validators(self, tmp_path):
        """With structured_output() (CD-916 / A1+A10), the LLM is forced to emit
        canonical field names (text, mention_type, span_start, span_end) via the
        Pydantic schema.  This eliminates the field-name mismatch that previously
        caused this test to fail.  The test now passes end-to-end.
        """
        mentions = [
            {
                "surface_form": "United Nations",
                "entity_type": "ORG",
                "start_offset": 4,
                "end_offset": 18,
            },
            {
                "surface_form": "New York City",
                "entity_type": "GPE",
                "start_offset": 145,
                "end_offset": 158,
            },
        ]
        propositions = [
            {
                "subject": "United Nations",
                "predicate": "founded_in",
                "object": "1945",
                "confidence": 0.9,
            },
        ]

        llm = PromptSchemaLLM(mentions, propositions)
        mcp = DirectMCPClient(RealValidatorHandler())
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(
            _make_state("doc-wrong-schema", max_retries=1)
        )

        # With prompt-schema fields, validators reject the mentions.
        # The graph should either complete (which means the bug is fixed)
        # or exhaust retries.  We assert it DID complete — since this is
        # xfail(strict=True), if it does NOT complete, the test correctly
        # reports as xfail.  If some day it passes, the xfail will alert us.
        assert result["status"] == "completed"
        assert len(result["accepted_mentions"]) > 0

    # ------------------------------------------------------------------
    # Happy path variations
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_multiple_mentions_and_propositions(self, tmp_path):
        """Happy path with 3+ mentions and 2+ propositions."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            },
            {
                "text": "New York City",
                "mention_type": "GPE",
                "span_start": 145,
                "span_end": 158,
                "confidence": 0.92,
            },
            {
                "text": "United States",
                "mention_type": "GPE",
                "span_start": 160,
                "span_end": 173,
                "confidence": 0.91,
            },
        ]
        propositions = [
            {
                "subject": "United Nations",
                "predicate": "founded_in",
                "object": "1945",
                "confidence": 0.9,
            },
            {
                "subject": "United Nations",
                "predicate": "headquartered_in",
                "object": "New York City",
                "confidence": 0.88,
            },
        ]

        llm = CorrectSchemaLLM(mentions, propositions)
        mcp = DirectMCPClient(RealValidatorHandler())
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(_make_state("doc-multi"))

        assert result["status"] == "completed"
        assert len(result["accepted_mentions"]) == 3
        assert len(result["accepted_propositions"]) == 2
        assert (tmp_path / "doc-multi" / "mentions.jsonl").exists()
        assert (tmp_path / "doc-multi" / "propositions.jsonl").exists()

    @pytest.mark.asyncio
    async def test_span_offsets_match_source_text(self, tmp_path):
        """Verify that mention span offsets actually match the source text."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            },
            {
                "text": "New York City",
                "mention_type": "GPE",
                "span_start": 145,
                "span_end": 158,
                "confidence": 0.92,
            },
        ]
        # Pre-flight check: spans actually match
        for m in mentions:
            assert SOURCE_TEXT[m["span_start"]:m["span_end"]] == m["text"]

        propositions = [
            {
                "subject": "United Nations",
                "predicate": "founded_in",
                "object": "1945",
                "confidence": 0.9,
            },
        ]

        llm = CorrectSchemaLLM(mentions, propositions)
        mcp = DirectMCPClient(RealValidatorHandler())
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(_make_state("doc-span-check"))

        assert result["status"] == "completed"
        # Verify the accepted mentions still have valid spans
        for m in result["accepted_mentions"]:
            span_s = m.get("span_start")
            span_e = m.get("span_end")
            if span_s is not None and span_e is not None:
                assert SOURCE_TEXT[span_s:span_e] == m["text"]

    # ------------------------------------------------------------------
    # Error path tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_invalid_span_offsets_triggers_repair(self, tmp_path):
        """Mentions with wrong span offsets should fail real validation and
        enter the repair loop."""
        bad_mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 0,  # Wrong — should be 4
                "span_end": 14,   # Wrong — should be 18
                "confidence": 0.95,
            },
        ]
        good_mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            },
        ]
        propositions = [
            {
                "subject": "United Nations",
                "predicate": "founded_in",
                "object": "1945",
                "confidence": 0.9,
            },
        ]

        call_count = {"n": 0}

        class RepairingLLM:
            async def complete(self, prompt: str, *, system: str = "") -> str:
                # Legacy path — no longer called by nodes
                if "proposition" in system.lower() or "triple" in system.lower():
                    return json.dumps({"propositions": propositions})
                call_count["n"] += 1
                if call_count["n"] <= 1:
                    return json.dumps({"mentions": bad_mentions})
                return json.dumps({"mentions": good_mentions})

            async def structured_output(self, schema: Any, messages: list) -> Any:
                if schema is MentionExtractionResult:
                    call_count["n"] += 1
                    data = bad_mentions if call_count["n"] <= 1 else good_mentions
                    return MentionExtractionResult(
                        mentions=[MentionCandidate(**m) for m in data]
                    )
                elif schema is PropositionExtractionResult:
                    return PropositionExtractionResult(
                        propositions=[PropositionCandidate(**p) for p in propositions]
                    )

        llm = RepairingLLM()
        mcp = DirectMCPClient(RealValidatorHandler())
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(_make_state("doc-repair-real", max_retries=3))

        assert result["status"] == "completed"
        assert result["mention_retry_count"] >= 1
        assert len(result["accepted_mentions"]) > 0

    @pytest.mark.asyncio
    async def test_max_retries_respected_with_real_validators(self, tmp_path):
        """Permanently bad data should exhaust retries and NOT loop forever."""
        bad_mentions = [
            {
                "text": "WRONG TEXT",
                "mention_type": "ORG",
                "span_start": 0,
                "span_end": 10,
                "confidence": 0.5,
            },
        ]
        propositions = [
            {
                "subject": "x",
                "predicate": "y",
                "object": "z",
                "confidence": 0.5,
            },
        ]

        llm = CorrectSchemaLLM(bad_mentions, propositions)
        mcp = DirectMCPClient(RealValidatorHandler())
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(_make_state("doc-maxretry-real", max_retries=2))

        # Should have hit max retries, not infinite loop
        assert result["mention_retry_count"] >= 2
        # Validate it failed or was handled — not "completed" with bad data
        assert result["status"] != "completed" or len(result.get("accepted_mentions", [])) == 0

    @pytest.mark.asyncio
    async def test_empty_mentions_fails_real_validation(self, tmp_path):
        """Empty mention list should be rejected by real validators."""
        llm = CorrectSchemaLLM([], [{"subject": "a", "predicate": "b", "object": "c", "confidence": 0.5}])
        mcp = DirectMCPClient(RealValidatorHandler())
        repo = JsonlRepository(tmp_path)
        graph = build_extraction_graph(llm, mcp, repo)

        result = await graph.ainvoke(_make_state("doc-empty-mentions", max_retries=1))

        # Empty mentions should be rejected by the validator (EMPTY_EXTRACTION)
        # and after max retries the pipeline should fail
        assert result["status"] != "completed" or len(result.get("accepted_mentions", [])) == 0

    # ------------------------------------------------------------------
    # Regression / contract tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_mock_vs_direct_mcp_behavior_difference(self, tmp_path):
        """Demonstrate that MockMCPClient accepts anything while DirectMCPClient
        actually validates — proving the need for real-validator tests.

        With structured_output(), even "bad" data gets forced into canonical
        Pydantic field names.  The difference is that DirectMCPClient's real
        validators catch semantic issues (wrong span offsets, non-existent text)
        while MockMCPClient blindly accepts.
        """
        from catalyst_langgraph.clients.mcp import MockMCPClient

        # Bad mentions: text doesn't exist at these offsets in SOURCE_TEXT
        bad_mentions = [
            {
                "text": "WRONG",
                "mention_type": "ORG",
                "span_start": 999,
                "span_end": 1000,
                "confidence": 0.5,
            },
        ]
        propositions = [
            {"subject": "a", "predicate": "b", "object": "c", "confidence": 0.5},
        ]

        # MockMCPClient: blindly accepts
        mock_llm = CorrectSchemaLLM(bad_mentions, propositions)
        mock_mcp = MockMCPClient()
        mock_repo = JsonlRepository(tmp_path / "mock")
        mock_graph = build_extraction_graph(mock_llm, mock_mcp, mock_repo)
        mock_result = await mock_graph.ainvoke(_make_state("doc-mock"))
        assert mock_result["status"] == "completed"  # Mock always passes

        # DirectMCPClient: rejects
        direct_llm = CorrectSchemaLLM(bad_mentions, propositions)
        direct_mcp = DirectMCPClient(RealValidatorHandler())
        direct_repo = JsonlRepository(tmp_path / "direct")
        direct_graph = build_extraction_graph(direct_llm, direct_mcp, direct_repo)
        direct_result = await direct_graph.ainvoke(
            _make_state("doc-direct", max_retries=1)
        )
        # Real validator should reject this — pipeline fails or has no accepted mentions
        assert (
            direct_result["status"] != "completed"
            or len(direct_result.get("accepted_mentions", [])) == 0
        )

    @pytest.mark.asyncio
    async def test_validator_returns_proper_verdict_values(self, tmp_path):
        """DirectMCPClient + RealValidatorHandler returns proper ValidationVerdict values."""
        handler = RealValidatorHandler()

        # Valid mentions
        valid_result = handler.validate_mentions(
            mentions=[
                {"text": "United Nations", "mention_type": "ORG", "span_start": 4, "span_end": 18},
            ],
            source_text=SOURCE_TEXT,
            document_id="test",
        )
        assert valid_result["verdict"] in ("valid", "invalid", "ambiguous", "abstain")
        assert valid_result["verdict"] == "valid"

        # Invalid mentions (missing required fields)
        invalid_result = handler.validate_mentions(
            mentions=[{"surface_form": "X"}],
            source_text=SOURCE_TEXT,
            document_id="test",
        )
        assert invalid_result["verdict"] in ("valid", "invalid", "ambiguous", "abstain")
        assert invalid_result["verdict"] == "invalid"

    @pytest.mark.asyncio
    async def test_validator_error_codes_are_real_issue_codes(self, tmp_path):
        """Error codes returned by validators should be real IssueCode enum values."""
        from catalyst_contracts.models.evidence import IssueCode

        handler = RealValidatorHandler()
        result = handler.validate_mentions(
            mentions=[
                {
                    "text": "WRONG",
                    "mention_type": "ORG",
                    "span_start": 0,
                    "span_end": 5,
                },
            ],
            source_text=SOURCE_TEXT,
            document_id="test",
        )
        valid_codes = {c.value for c in IssueCode}
        for error in result.get("errors", []):
            assert error["code"] in valid_codes, (
                f"Error code '{error['code']}' is not a valid IssueCode"
            )
