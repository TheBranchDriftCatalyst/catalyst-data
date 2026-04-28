"""Behavioral tests for ensemble extraction and consensus voting.

Tests cover ConsensusVoter voting strategies (majority, unanimous, any)
and EnsembleExtractNode async execution with fault tolerance.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from catalyst_exgraph.config import ner_stage_config
from catalyst_exgraph.ensemble import ConsensusVoter, EnsembleExtractNode
from catalyst_exgraph.state import ExGraphState, ExGraphStatus

from .conftest import (
    SAMPLE_MENTIONS,
    SAMPLE_TEXT,
    MockExtractionClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROMPT_PATCH = patch(
    "catalyst_exgraph.nodes.extract._load_prompt",
    return_value="You are an extraction assistant.",
)


def _make_input_state(text: str = SAMPLE_TEXT, **overrides) -> ExGraphState:
    """Build a minimal ExGraphState for EnsembleExtractNode invocation."""
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


def _mention(text: str, mention_type: str, confidence: float = 1.0) -> dict:
    """Shorthand for building a mention dict."""
    return {
        "text": text,
        "mention_type": mention_type,
        "span_start": 0,
        "span_end": len(text),
        "confidence": confidence,
    }


def _prop(subj: str, pred: str, obj: str, confidence: float = 0.9) -> dict:
    """Shorthand for building a proposition dict."""
    return {
        "subject": subj,
        "predicate": pred,
        "object": obj,
        "confidence": confidence,
        "evidence": f"{subj} {pred} {obj}",
    }


# =====================================================================
# ConsensusVoter — majority strategy
# =====================================================================


class TestConsensusVoterMajority:
    """Tests for majority voting strategy (threshold=0.5 by default)."""

    def test_majority_2_of_3_agree_item_accepted(self):
        """Item found by 2/3 models passes majority threshold (0.667 >= 0.5)."""
        m = _mention("Acme Corp", "ORG")
        voter = ConsensusVoter(strategy="majority", threshold=0.5, kind="ner")
        results = {
            "model_a": [m],
            "model_b": [m],
            "model_c": [],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 1
        assert accepted[0]["consensus_score"] == pytest.approx(0.667, abs=0.001)

    def test_majority_1_of_3_agree_item_rejected(self):
        """Item found by 1/3 models is below majority threshold (0.333 < 0.5)."""
        m = _mention("Acme Corp", "ORG")
        voter = ConsensusVoter(strategy="majority", threshold=0.5, kind="ner")
        results = {
            "model_a": [m],
            "model_b": [],
            "model_c": [],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 0


# =====================================================================
# ConsensusVoter — unanimous strategy
# =====================================================================


class TestConsensusVoterUnanimous:
    """Tests for unanimous voting strategy."""

    def test_unanimous_all_3_agree_item_accepted(self):
        """Item found by all 3 models is accepted with consensus_score=1.0."""
        m = _mention("Acme Corp", "ORG")
        voter = ConsensusVoter(strategy="unanimous", kind="ner")
        results = {
            "model_a": [m],
            "model_b": [m],
            "model_c": [m],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 1
        assert accepted[0]["consensus_score"] == 1.0

    def test_unanimous_2_of_3_agree_item_rejected(self):
        """Item found by 2/3 models is rejected — unanimous requires all."""
        m = _mention("Acme Corp", "ORG")
        voter = ConsensusVoter(strategy="unanimous", kind="ner")
        results = {
            "model_a": [m],
            "model_b": [m],
            "model_c": [],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 0


# =====================================================================
# ConsensusVoter — any strategy
# =====================================================================


class TestConsensusVoterAny:
    """Tests for 'any' voting strategy (union — accept if any model found it)."""

    def test_any_1_of_3_agree_item_accepted(self):
        """Item found by just 1/3 models is accepted with 'any' strategy."""
        m = _mention("Acme Corp", "ORG")
        voter = ConsensusVoter(strategy="any", kind="ner")
        results = {
            "model_a": [m],
            "model_b": [],
            "model_c": [],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 1


# =====================================================================
# ConsensusVoter — edge cases
# =====================================================================


class TestConsensusVoterEdgeCases:
    """Edge cases: empty input, single model, partial overlap."""

    def test_empty_model_results_returns_empty(self):
        """No models at all yields empty output."""
        voter = ConsensusVoter(strategy="majority", kind="ner")
        accepted = voter.vote({})
        assert accepted == []

    def test_single_model_passthrough(self):
        """Single model (N=1) is effectively pass-through with consensus_score=1.0."""
        m = _mention("Alice", "PERSON")
        voter = ConsensusVoter(strategy="majority", threshold=0.5, kind="ner")
        results = {"only_model": [m]}
        accepted = voter.vote(results)
        assert len(accepted) == 1
        assert accepted[0]["consensus_score"] == 1.0
        assert accepted[0]["text"] == "Alice"

    def test_partial_overlap_only_shared_items_accepted(self):
        """model A finds [X, Y], model B finds [Y, Z] -> only Y accepted (majority 2/2)."""
        x = _mention("Acme Corp", "ORG")
        y = _mention("Bob Jones", "PERSON")
        z = _mention("New York", "GPE")
        voter = ConsensusVoter(strategy="majority", threshold=0.5, kind="ner")
        results = {
            "model_a": [x, y],
            "model_b": [y, z],
        }
        accepted = voter.vote(results)
        # With threshold=0.5 and 2 models: 1/2=0.5 passes, 2/2=1.0 passes
        # All items appear in at least 1 model -> 1/2=0.5 >= 0.5 -> all pass
        # X appears in 1/2=0.5 >= 0.5 -> accepted
        # Y appears in 2/2=1.0 >= 0.5 -> accepted
        # Z appears in 1/2=0.5 >= 0.5 -> accepted
        assert len(accepted) == 3

    def test_partial_overlap_strict_threshold_rejects_unique_items(self):
        """With threshold > 0.5, items unique to one model of two are rejected."""
        x = _mention("Acme Corp", "ORG")
        y = _mention("Bob Jones", "PERSON")
        z = _mention("New York", "GPE")
        voter = ConsensusVoter(strategy="majority", threshold=0.6, kind="ner")
        results = {
            "model_a": [x, y],
            "model_b": [y, z],
        }
        accepted = voter.vote(results)
        # X: 1/2=0.5 < 0.6 -> rejected
        # Y: 2/2=1.0 >= 0.6 -> accepted
        # Z: 1/2=0.5 < 0.6 -> rejected
        assert len(accepted) == 1
        assert accepted[0]["text"] == "Bob Jones"


# =====================================================================
# ConsensusVoter — score accuracy & metadata
# =====================================================================


class TestConsensusVoterScoresAndMetadata:
    """Verify exact consensus score values and metadata fields."""

    def test_consensus_score_exact_values_1_2_3_of_3(self):
        """Verify exact float values for 1/3, 2/3, 3/3 consensus scores."""
        a = _mention("Acme Corp", "ORG")
        b = _mention("Bob Jones", "PERSON")
        c = _mention("New York", "GPE")
        voter = ConsensusVoter(strategy="any", kind="ner")
        results = {
            "model_a": [a, b, c],
            "model_b": [b, c],
            "model_c": [c],
        }
        accepted = voter.vote(results)
        scores = {item["text"]: item["consensus_score"] for item in accepted}
        assert scores["Acme Corp"] == pytest.approx(0.333, abs=0.001)
        assert scores["Bob Jones"] == pytest.approx(0.667, abs=0.001)
        assert scores["New York"] == 1.0

    def test_contributing_models_populated(self):
        """Accepted items list the specific models that found them."""
        m = _mention("Acme Corp", "ORG")
        voter = ConsensusVoter(strategy="majority", threshold=0.5, kind="ner")
        results = {
            "model_a": [m],
            "model_b": [m],
            "model_c": [],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 1
        assert set(accepted[0]["contributing_models"]) == {"model_a", "model_b"}

    def test_ensemble_size_metadata_populated(self):
        """Accepted items include ensemble_size indicating total number of models."""
        m = _mention("Acme Corp", "ORG")
        voter = ConsensusVoter(strategy="any", kind="ner")
        results = {"m1": [m], "m2": [], "m3": []}
        accepted = voter.vote(results)
        assert accepted[0]["ensemble_size"] == 3

    def test_higher_confidence_preferred(self):
        """When multiple models find the same entity, the item with highest confidence is kept."""
        low = _mention("Acme Corp", "ORG", confidence=0.5)
        high = _mention("Acme Corp", "ORG", confidence=0.95)
        voter = ConsensusVoter(strategy="majority", threshold=0.5, kind="ner")
        results = {
            "model_a": [low],
            "model_b": [high],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 1
        assert accepted[0]["confidence"] == 0.95


# =====================================================================
# ConsensusVoter — SPO normalization
# =====================================================================


class TestConsensusVoterSPO:
    """Tests for SPO (proposition) normalization and voting."""

    def test_spo_normalization_matches_by_subject_predicate_object(self):
        """Propositions with same (subj, pred, obj) normalized to lowercase are matched."""
        p1 = _prop("John Smith", "works_for", "Acme Corp", confidence=0.8)
        # Different casing but same triple
        p2 = _prop("john smith", "Works_For", "acme corp", confidence=0.9)
        voter = ConsensusVoter(strategy="majority", threshold=0.5, kind="spo")
        results = {
            "model_a": [p1],
            "model_b": [p2],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 1
        assert accepted[0]["consensus_score"] == 1.0
        # Higher confidence version kept
        assert accepted[0]["confidence"] == 0.9

    def test_spo_different_predicates_not_merged(self):
        """Propositions with different predicates are distinct items."""
        p1 = _prop("John", "works_for", "Acme")
        p2 = _prop("John", "founded", "Acme")
        voter = ConsensusVoter(strategy="any", kind="spo")
        results = {
            "model_a": [p1],
            "model_b": [p2],
        }
        accepted = voter.vote(results)
        assert len(accepted) == 2


# =====================================================================
# EnsembleExtractNode — async behavior
# =====================================================================


class TestEnsembleExtractNode:
    """Async tests for EnsembleExtractNode execution."""

    @_PROMPT_PATCH
    async def test_runs_all_clients_with_same_messages(self, _prompt):
        """Both mock clients are called with the same messages."""
        client_a = MockExtractionClient(mentions=SAMPLE_MENTIONS)
        client_b = MockExtractionClient(mentions=SAMPLE_MENTIONS)
        config = ner_stage_config()
        node = EnsembleExtractNode(config, {"model_a": client_a, "model_b": client_b})
        state = _make_input_state()

        await node(state)

        assert len(client_a.structured_calls) == 1
        assert len(client_b.structured_calls) == 1
        # Both receive the same message list
        _, msgs_a = client_a.structured_calls[0]
        _, msgs_b = client_b.structured_calls[0]
        assert len(msgs_a) == len(msgs_b) == 2  # SystemMessage + HumanMessage

    @_PROMPT_PATCH
    async def test_fault_tolerance_one_client_fails(self, _prompt):
        """If one client raises, the other's results are still used."""

        class FailingClient:
            model = "failing-model"
            structured_method = "mock"

            async def structured_output(self, schema, messages):
                raise RuntimeError("model crashed")

        good_client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
        config = ner_stage_config()
        node = EnsembleExtractNode(
            config, {"good": good_client, "bad": FailingClient()}
        )
        state = _make_input_state()

        result = await node(state)

        stage = result["stages"]["ner"]
        # Even with one failure, good model's results pass through
        # (single model of 2 -> 0.5, which meets threshold 0.5)
        assert stage["status"] == "validating"
        assert len(stage["candidates"]) > 0

    @_PROMPT_PATCH
    async def test_audit_event_records_model_details(self, _prompt):
        """Audit event captures per_model_counts, strategy, and errors."""
        client_a = MockExtractionClient(mentions=SAMPLE_MENTIONS)
        client_b = MockExtractionClient(mentions=SAMPLE_MENTIONS)
        config = ner_stage_config()
        node = EnsembleExtractNode(config, {"model_a": client_a, "model_b": client_b})
        state = _make_input_state()

        result = await node(state)

        assert len(result["audit_events"]) == 1
        audit = result["audit_events"][0]
        details = audit["details"]
        assert set(details["models"]) == {"model_a", "model_b"}
        assert "model_a" in details["per_model_counts"]
        assert "model_b" in details["per_model_counts"]
        assert details["strategy"] == "majority"
        assert details["threshold"] == 0.5
        assert details["errors"] == []
        assert audit["status"] == "completed"
        assert audit["duration_s"] > 0

    @_PROMPT_PATCH
    async def test_audit_event_records_errors_on_failure(self, _prompt):
        """Audit event captures error messages when a model fails."""

        class FailingClient:
            model = "failing-model"
            structured_method = "mock"

            async def structured_output(self, schema, messages):
                raise ValueError("bad response")

        good_client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
        config = ner_stage_config()
        node = EnsembleExtractNode(
            config, {"good": good_client, "bad": FailingClient()}
        )
        state = _make_input_state()

        result = await node(state)

        audit = result["audit_events"][0]
        assert len(audit["details"]["errors"]) == 1
        assert "bad: bad response" in audit["details"]["errors"][0]

    @_PROMPT_PATCH
    async def test_all_clients_fail_produces_empty_candidates(self, _prompt):
        """When every client fails, stage has no candidates and status is completed."""

        class FailingClient:
            model = "failing-model"
            structured_method = "mock"

            async def structured_output(self, schema, messages):
                raise RuntimeError("boom")

        config = ner_stage_config()
        node = EnsembleExtractNode(
            config, {"bad1": FailingClient(), "bad2": FailingClient()}
        )
        state = _make_input_state()

        result = await node(state)

        stage = result["stages"]["ner"]
        assert stage["candidates"] == []
        assert stage["status"] == "completed"
        assert result["status"] == ExGraphStatus.COMPLETED.value

    @_PROMPT_PATCH
    async def test_consensus_results_written_to_stage_state(self, _prompt):
        """Consensus items are written to stages[stage_name]['candidates']."""
        client_a = MockExtractionClient(mentions=SAMPLE_MENTIONS)
        client_b = MockExtractionClient(mentions=SAMPLE_MENTIONS)
        config = ner_stage_config()
        node = EnsembleExtractNode(config, {"model_a": client_a, "model_b": client_b})
        state = _make_input_state()

        result = await node(state)

        stage = result["stages"]["ner"]
        assert stage["status"] == "validating"
        assert len(stage["candidates"]) == len(SAMPLE_MENTIONS)
        for item in stage["candidates"]:
            assert "consensus_score" in item
            assert item["consensus_score"] == 1.0  # both models agree
            assert set(item["contributing_models"]) == {"model_a", "model_b"}
