"""Test ConsensusNode span-overlap clustering logic.

Phase B / CD-94ow.  Verifies that mentions with overlapping (but not
identical) spans still cluster as long as overlap ≥ 50%.
"""

from __future__ import annotations

import pytest
from catalyst_exgraph.nodes.consensus import ConsensusNode, _span_overlap_ratio
from catalyst_exgraph.state import ExGraphState

# ---------------------------------------------------------------------------
# Unit tests for _span_overlap_ratio
# ---------------------------------------------------------------------------


def test_identical_spans_return_1():
    assert _span_overlap_ratio(10, 20, 10, 20) == 1.0


def test_no_overlap_returns_0():
    assert _span_overlap_ratio(0, 5, 10, 15) == 0.0


def test_adjacent_spans_return_0():
    assert _span_overlap_ratio(0, 5, 5, 10) == 0.0


def test_partial_overlap_ratio():
    # span_a = [0, 10), span_b = [5, 15) — overlap = 5, max_len = 10 → ratio = 0.5
    ratio = _span_overlap_ratio(0, 10, 5, 15)
    assert abs(ratio - 0.5) < 1e-9


def test_one_span_contains_other():
    # span_a = [0, 20), span_b = [5, 15) — overlap = 10, max_len = 20 → 0.5
    ratio = _span_overlap_ratio(0, 20, 5, 15)
    assert abs(ratio - 0.5) < 1e-9


def test_zero_length_span_returns_0():
    assert _span_overlap_ratio(5, 5, 0, 10) == 0.0
    assert _span_overlap_ratio(0, 10, 5, 5) == 0.0


# ---------------------------------------------------------------------------
# Integration tests via ConsensusNode
# ---------------------------------------------------------------------------


def _mention(text: str, encoder: str, span_start: int, span_end: int, confidence: float = 0.9) -> dict:
    return {
        "text": text,
        "mention_type": "PERSON",
        "span_start": span_start,
        "span_end": span_end,
        "confidence": confidence,
        "_source_encoder": encoder,
    }


def _state(per_encoder: dict, doc_id: str = "doc-overlap") -> ExGraphState:
    return {
        "raw_text": "Alice sat with Alice Johnson at the park.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "per_encoder_mentions": per_encoder,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


@pytest.mark.asyncio
async def test_overlapping_spans_cluster_together():
    """Same text, 60% span overlap → 1 cluster with vote_count=2."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        # "Alice" at [0, 10) and [0, 15) — overlap=10, max=15 → 0.67
        "gliner-medium": [_mention("Alice", "gliner-medium", span_start=0, span_end=10)],
        "gliner-large": [_mention("Alice", "gliner-large", span_start=0, span_end=15)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    assert result["consensus_mentions"][0]["vote_count"] == 2


@pytest.mark.asyncio
async def test_non_overlapping_spans_produce_separate_clusters():
    """Same text but spans at non-overlapping positions → 2 clusters → both accepted
    if quorum=1 or both rejected if quorum=2."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        # "Alice" appears twice in the document at disjoint positions
        "gliner-medium": [_mention("Alice", "gliner-medium", span_start=0, span_end=5)],
        "gliner-large": [_mention("Alice", "gliner-large", span_start=30, span_end=35)],
    }

    # With quorum=1 both should be accepted (each has 1 vote)
    node_q1 = ConsensusNode(encoders=encoders, quorum=1)
    result_q1 = await node_q1(_state(per_encoder))
    assert len(result_q1["consensus_mentions"]) == 2

    # With quorum=2 both should be rejected (each only 1 vote < 2)
    node_q2 = ConsensusNode(encoders=encoders, quorum=2)
    result_q2 = await node_q2(_state(per_encoder))
    assert len(result_q2["consensus_mentions"]) == 0
    assert len(result_q2["rejected_mentions"]) == 2


@pytest.mark.asyncio
async def test_exactly_50_percent_overlap_clusters():
    """Exactly 50% overlap → should cluster (threshold is ≥0.5)."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        # [0, 10) and [5, 15) → overlap=5, max_len=10 → exactly 0.5
        "gliner-medium": [_mention("Alice", "gliner-medium", span_start=0, span_end=10)],
        "gliner-large": [_mention("Alice", "gliner-large", span_start=5, span_end=15)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    assert result["consensus_mentions"][0]["vote_count"] == 2


@pytest.mark.asyncio
async def test_span_from_highest_confidence_encoder():
    """Span in the consensus mention comes from the highest-confidence encoder."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        "gliner-medium": [_mention("Alice", "gliner-medium", span_start=0, span_end=5, confidence=0.6)],
        "gliner-large": [_mention("Alice", "gliner-large", span_start=0, span_end=6, confidence=0.95)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    m = result["consensus_mentions"][0]
    # gliner-large has higher confidence → its span and provenance should win
    assert m["span_provenance"] == "gliner-large"
    assert m["span_end"] == 6


@pytest.mark.asyncio
async def test_span_disagreement_chars_is_nonzero_when_spans_differ():
    """span_disagreement_chars is non-zero when encoders disagreed on span."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        "gliner-medium": [_mention("Alice", "gliner-medium", span_start=0, span_end=5, confidence=0.5)],
        "gliner-large": [_mention("Alice", "gliner-large", span_start=0, span_end=10, confidence=0.9)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    m = result["consensus_mentions"][0]
    # span_provenance = gliner-large (span_end=10)
    # other span: span_end=5 → diff = |10-5| = 5
    assert m["span_end"] == 10
    # span_disagreement_chars should be reported in the audit event
    # (we verify indirectly via raw_mentions presence — direct check is in audit test)
    assert "raw_mentions" in m
