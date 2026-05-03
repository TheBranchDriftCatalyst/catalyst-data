"""Test that the SPO loop in extract_validated passes full ConsensusMention dicts.

Phase D / CD-3w3n.  Simulates the _process_doc SPO loop with a NER result that
carries consensus_mentions.  Asserts that each SPO sub-graph invocation receives
upstream_context.accepted_mentions with 'vote_count' fields (not bare strings).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dagster_io.extraction import _Doc, _process_doc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _consensus_mention(
    text: str,
    canonical_type: str = "PERSON",
    vote_count: int = 5,
    n_encoders: int = 5,
    mean_confidence: float = 0.90,
) -> dict:
    return {
        "mention_id": f"mid-{text.lower()}",
        "text": text,
        "canonical_type": canonical_type,
        "vote_count": vote_count,
        "n_encoders": n_encoders,
        "mean_confidence": mean_confidence,
        "source_models": ["enc-a", "enc-b"],
        "type_votes": {canonical_type: vote_count},
        "span_start": 0,
        "span_end": len(text),
        "span_provenance": "enc-a",
    }


def _ner_result_with_consensus(mentions: list[dict], windows: list[dict]) -> dict:
    """Fake NER pipeline output that includes consensus_mentions."""
    return {
        "stages": {
            "ner": {
                "accepted": mentions,
                "retry_count": 0,
                "status": "completed",
            }
        },
        "evidence_windows": windows,
        "consensus_mentions": mentions,  # same list for test simplicity
        "audit_events": [],
        "status": "completed",
    }


def _make_doc(doc_id: str = "doc-plumb") -> _Doc:
    """Build a minimal _Doc for testing."""

    class _Chunk:
        chunk_id = "chunk-plumb"
        index = 0
        text = "Reagan met Putin at Crimea."

    chunk = _Chunk()
    return _Doc(
        doc_id=doc_id,
        full_text="Reagan met Putin at Crimea.",
        chunks=[chunk],
        chunk_metadata={"domain": "test"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spo_upstream_context_receives_consensus_mention_dicts():
    """When NER result carries consensus_mentions, SPO gets full ConsensusMention dicts."""
    mentions = [
        _consensus_mention("Reagan", vote_count=5),
        _consensus_mention("Putin", vote_count=4, mean_confidence=0.87),
        _consensus_mention("Crimea", canonical_type="LOCATION", vote_count=3, mean_confidence=0.62),
    ]

    window = {
        "window_id": "win-0",
        "text": "Reagan met Putin at Crimea.",
        "doc_char_start": 0,
        "doc_char_end": 27,
        "mention_indices": [0, 1, 2],
        "cluster_id": "cl-0",
    }

    ner_result = _ner_result_with_consensus(mentions, [window])

    # Captured SPO ainvoke calls
    captured_inputs: list[dict] = []

    async def _fake_spo_invoke(state_input: dict) -> dict:
        captured_inputs.append(state_input)
        return {
            "stages": {"spo": {"accepted": [], "retry_count": 0, "status": "completed"}},
            "audit_events": [],
            "status": "completed",
        }

    mock_ner_pipeline = AsyncMock()
    mock_ner_pipeline.ainvoke = AsyncMock(return_value=ner_result)

    mock_spo_pipeline = AsyncMock()
    mock_spo_pipeline.ainvoke = AsyncMock(side_effect=_fake_spo_invoke)

    doc = _make_doc()

    result = await _process_doc(
        ner_pipeline=mock_ner_pipeline,
        spo_pipeline=mock_spo_pipeline,
        doc=doc,
        bench_model="test-model",
        max_retries=0,
    )

    assert result["status"] == "completed"
    assert len(captured_inputs) == 1, "Expected exactly one SPO invocation (one window)"

    spo_input = captured_inputs[0]
    accepted = spo_input["upstream_context"]["accepted_mentions"]

    assert len(accepted) == 3, f"Expected 3 mentions; got {len(accepted)}"

    # All should be full ConsensusMention dicts (not bare strings)
    for m in accepted:
        assert "vote_count" in m, f"Missing 'vote_count' in mention: {m}"
        assert "mean_confidence" in m, f"Missing 'mean_confidence' in mention: {m}"
        assert "canonical_type" in m, f"Missing 'canonical_type' in mention: {m}"

    # Spot-check values
    reagan = next(m for m in accepted if m["text"] == "Reagan")
    assert reagan["vote_count"] == 5
    crimea = next(m for m in accepted if m["text"] == "Crimea")
    assert crimea["canonical_type"] == "LOCATION"


@pytest.mark.asyncio
async def test_spo_upstream_context_falls_back_without_consensus():
    """When consensus_mentions is absent, SPO gets legacy NER accepted dicts."""
    bare_mentions = [
        {"text": "Reagan", "mention_type": "PERSON", "span_start": 0, "span_end": 6, "confidence": 0.9},
        {"text": "Crimea", "mention_type": "LOCATION", "span_start": 15, "span_end": 21, "confidence": 0.85},
    ]

    window = {
        "window_id": "win-0",
        "text": "Reagan visited Crimea.",
        "doc_char_start": 0,
        "doc_char_end": 22,
        "mention_indices": [0, 1],
        "cluster_id": "cl-0",
    }

    # NER result WITHOUT consensus_mentions
    ner_result = {
        "stages": {
            "ner": {
                "accepted": bare_mentions,
                "retry_count": 0,
                "status": "completed",
            }
        },
        "evidence_windows": [window],
        "audit_events": [],
        "status": "completed",
        # consensus_mentions absent / empty
    }

    captured_inputs: list[dict] = []

    async def _fake_spo_invoke(state_input: dict) -> dict:
        captured_inputs.append(state_input)
        return {
            "stages": {"spo": {"accepted": [], "retry_count": 0, "status": "completed"}},
            "audit_events": [],
            "status": "completed",
        }

    mock_ner_pipeline = AsyncMock()
    mock_ner_pipeline.ainvoke = AsyncMock(return_value=ner_result)

    mock_spo_pipeline = AsyncMock()
    mock_spo_pipeline.ainvoke = AsyncMock(side_effect=_fake_spo_invoke)

    doc = _make_doc()

    result = await _process_doc(
        ner_pipeline=mock_ner_pipeline,
        spo_pipeline=mock_spo_pipeline,
        doc=doc,
        bench_model="test-model",
        max_retries=0,
    )

    assert result["status"] == "completed"
    assert len(captured_inputs) == 1

    accepted = captured_inputs[0]["upstream_context"]["accepted_mentions"]
    assert len(accepted) == 2

    # Legacy dicts — no vote_count field
    for m in accepted:
        assert "text" in m
        assert "vote_count" not in m, f"Legacy fallback should not have 'vote_count': {m}"
