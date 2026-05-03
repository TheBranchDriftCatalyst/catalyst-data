"""Test: _run_model (LLM-tier) passes ConsensusMention dicts to SPO (CD-z6xe).

Verifies that the SPO pipeline's ``upstream_context.accepted_mentions`` contains
ConsensusMention dicts (with ``vote_count`` and ``n_encoders`` fields) when
``shared_mentions`` is populated with consensus output from Phase A.

The test mocks ``extract_with_shared_clusters`` so no live LLM is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.benchmark_config import ModelConfig

# ── Helper builders ───────────────────────────────────────────────────────────


def _consensus_mention(text: str) -> dict:
    return {
        "text": text,
        "canonical_type": "PERSON",
        "vote_count": 2,
        "n_encoders": 3,
        "mean_confidence": 0.88,
        "source_models": ["gliner-large", "gliner-pii"],
    }


def _make_llm_cfg() -> ModelConfig:
    return ModelConfig(
        name="mistral-7b",
        model="mistral:latest",
        base_url="http://localhost:11434/v1",
        tags=["ollama", "7b", "tier2"],
    )


def _make_doc(doc_id: str, full_text: str = "test text"):
    from dagster_io.extraction import _Doc

    return _Doc(doc_id=doc_id, full_text=full_text, chunks=[], chunk_metadata={})


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestPhaseBSpoUsesConsensus:
    """LLM-tier _run_model passes consensus mentions into extract_with_shared_clusters."""

    def _run_llm_model(self, shared_mentions: dict):
        """Run _run_model for an LLM-tier model with the given shared_mentions."""
        from tests.benchmark_harness import _run_model

        cfg = _make_llm_cfg()
        docs = [_make_doc("doc-1", "Alice met Bob at Acme.")]
        clusters = {"doc-1": [{"cluster_id": "cl-0", "mention_indices": [0]}]}

        # Capture the shared_mentions argument passed to extract_with_shared_clusters
        captured: dict = {}

        def _fake_extract(docs_, clusters_, *, shared_mentions=None, code_location, max_concurrency):
            captured["shared_mentions"] = shared_mentions
            # Return trivial Mention + Assertion objects
            mock_mention = MagicMock()
            mock_mention.model_dump.return_value = {"text": "Alice", "mention_type": "PERSON"}
            mock_assertion = MagicMock()
            mock_assertion.model_dump.return_value = {"subject": "Alice", "predicate": "met", "object": "Bob"}
            return [mock_mention], [mock_assertion]

        fake_store = MagicMock()
        fake_store.load_run.return_value = None

        with (
            patch("dagster_io.extraction.extract_with_shared_clusters", side_effect=_fake_extract),
            patch(
                "os.environ",
                dict(
                    BENCH_SAMPLE_PER_DOMAIN="5",
                    LLM_MODEL="mistral:latest",
                    LLM_BASE_URL="http://localhost:11434/v1",
                    PROMPT_REGISTRY_DIR="/tmp",
                    CATALYST_BENCH_MODEL="mistral-7b",
                ),
            ),
        ):
            fixture = _run_model(
                cfg=cfg,
                timeout=30,
                store=fake_store,
                run_id="test-run",
                shared_docs=docs,
                shared_clusters=clusters,
                shared_mentions=shared_mentions,
                phase_a_encoder_names=set(),
            )

        return fixture, captured

    def test_shared_mentions_forwarded_to_extract_with_shared_clusters(self):
        """extract_with_shared_clusters receives the shared_mentions dict."""
        consensus_mentions = {
            "doc-1": [_consensus_mention("Alice"), _consensus_mention("Bob")],
        }
        fixture, captured = self._run_llm_model(shared_mentions=consensus_mentions)

        assert "shared_mentions" in captured
        passed = captured["shared_mentions"]
        assert passed is not None
        assert "doc-1" in passed
        assert len(passed["doc-1"]) == 2

    def test_consensus_fields_present_in_forwarded_mentions(self):
        """ConsensusMention fields (vote_count, n_encoders) survive the handoff."""
        consensus_mentions = {
            "doc-1": [_consensus_mention("Alice")],
        }
        _, captured = self._run_llm_model(shared_mentions=consensus_mentions)

        passed = captured.get("shared_mentions", {})
        mention = passed.get("doc-1", [{}])[0]
        assert mention.get("vote_count") == 2
        assert mention.get("n_encoders") == 3
        assert mention.get("canonical_type") == "PERSON"

    def test_fixture_produced_for_llm_model(self):
        """LLM-tier model produces a non-None fixture with mentions + assertions."""
        consensus_mentions = {"doc-1": [_consensus_mention("Alice")]}
        fixture, _ = self._run_llm_model(shared_mentions=consensus_mentions)

        assert fixture is not None
        assert "mentions" in fixture
        assert "assertions" in fixture
        assert isinstance(fixture["mentions"], list)
        assert isinstance(fixture["assertions"], list)
