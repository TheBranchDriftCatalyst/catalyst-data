"""Test ConsensusNode span provenance and disagreement tracking.

Phase B / CD-94ow.  Verifies:
- span is taken from the highest-confidence encoder
- span_provenance names the source encoder
- span_disagreement_chars is computed correctly
- zero disagreement when all encoders have identical spans
"""

from __future__ import annotations

import pytest
from catalyst_exgraph.nodes.consensus import ConsensusNode
from catalyst_exgraph.state import ExGraphState


def _mention(text: str, encoder: str, span_start: int, span_end: int, confidence: float = 0.9) -> dict:
    return {
        "text": text,
        "mention_type": "PERSON",
        "span_start": span_start,
        "span_end": span_end,
        "confidence": confidence,
        "_source_encoder": encoder,
    }


def _state(per_encoder: dict, doc_id: str = "doc-prov") -> ExGraphState:
    return {
        "raw_text": "Alice sat with Alice Johnson.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "per_encoder_mentions": per_encoder,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


@pytest.mark.asyncio
async def test_span_from_highest_confidence():
    """The consensus mention uses the span from the highest-confidence encoder."""
    encoders = ["a", "b", "c"]
    per_encoder = {
        "a": [_mention("Alice", "a", span_start=0, span_end=5, confidence=0.5)],
        "b": [_mention("Alice", "b", span_start=0, span_end=5, confidence=0.99)],
        "c": [_mention("Alice", "c", span_start=0, span_end=6, confidence=0.7)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    m = result["consensus_mentions"][0]
    assert m["span_provenance"] == "b"
    assert m["span_end"] == 5


@pytest.mark.asyncio
async def test_span_provenance_in_raw_mentions():
    """raw_mentions list contains the per-encoder mention dicts."""
    encoders = ["a", "b"]
    per_encoder = {
        "a": [_mention("Alice", "a", span_start=0, span_end=5, confidence=0.8)],
        "b": [_mention("Alice", "b", span_start=0, span_end=5, confidence=0.9)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    m = result["consensus_mentions"][0]
    assert len(m["raw_mentions"]) == 2
    source_encoders = {rm.get("_source_encoder") for rm in m["raw_mentions"]}
    assert source_encoders == {"a", "b"}


@pytest.mark.asyncio
async def test_zero_span_disagreement_when_identical():
    """All encoders use the same span → span_disagreement tracked as zero."""
    # Compute disagreement directly via the static method
    cluster = [
        {"span_start": 5, "span_end": 10},
        {"span_start": 5, "span_end": 10},
        {"span_start": 5, "span_end": 10},
    ]
    disagree = ConsensusNode._span_disagreement(cluster, chosen_start=5, chosen_end=10)
    assert disagree == 0


@pytest.mark.asyncio
async def test_nonzero_span_disagreement_when_different():
    """Span disagreement = max(|start_diff| + |end_diff|) across cluster members."""
    # Chosen span: [0, 10). Another member: [2, 8). Diff = |0-2|+|10-8| = 4
    cluster = [
        {"span_start": 0, "span_end": 10},
        {"span_start": 2, "span_end": 8},
    ]
    disagree = ConsensusNode._span_disagreement(cluster, chosen_start=0, chosen_end=10)
    assert disagree == 4


@pytest.mark.asyncio
async def test_mean_confidence_across_cluster():
    """mean_confidence is the mean of all raw mention confidences in the cluster."""
    encoders = ["a", "b", "c"]
    per_encoder = {
        "a": [_mention("Alice", "a", span_start=0, span_end=5, confidence=0.6)],
        "b": [_mention("Alice", "b", span_start=0, span_end=5, confidence=0.8)],
        "c": [_mention("Alice", "c", span_start=0, span_end=5, confidence=1.0)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    m = result["consensus_mentions"][0]
    expected_mean = (0.6 + 0.8 + 1.0) / 3
    assert abs(m["mean_confidence"] - expected_mean) < 1e-6


@pytest.mark.asyncio
async def test_source_models_contains_all_contributing_encoders():
    """source_models lists every encoder that contributed to the cluster."""
    encoders = ["a", "b", "c", "d"]
    per_encoder = {
        "a": [_mention("Alice", "a", span_start=0, span_end=5)],
        "b": [_mention("Alice", "b", span_start=0, span_end=5)],
        "c": [_mention("Alice", "c", span_start=0, span_end=5)],
        "d": [],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    m = result["consensus_mentions"][0]
    assert set(m["source_models"]) == {"a", "b", "c"}
    assert "d" not in m["source_models"]


@pytest.mark.asyncio
async def test_n_encoders_reflects_ensemble_size():
    """n_encoders is the total ensemble size, not just voters."""
    encoders = ["a", "b", "c", "d", "e"]
    per_encoder = {
        "a": [_mention("Alice", "a", span_start=0, span_end=5)],
        "b": [_mention("Alice", "b", span_start=0, span_end=5)],
        "c": [_mention("Alice", "c", span_start=0, span_end=5)],
        "d": [],
        "e": [],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    m = result["consensus_mentions"][0]
    assert m["n_encoders"] == 5
    assert m["vote_count"] == 3
