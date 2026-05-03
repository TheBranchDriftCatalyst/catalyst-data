"""Test ConsensusNode quorum filtering.

Phase B / CD-94ow.  Verifies default ceil(N/2) quorum behaviour,
configurable quorum override, and per-type quorum.
"""

from __future__ import annotations

import math

import pytest
from catalyst_exgraph.nodes.consensus import ConsensusNode
from catalyst_exgraph.state import ExGraphState


def _mention(text: str, encoder: str, mention_type: str = "PERSON", span_start: int = 0) -> dict:
    return {
        "text": text,
        "mention_type": mention_type,
        "span_start": span_start,
        "span_end": span_start + len(text),
        "confidence": 0.9,
        "_source_encoder": encoder,
    }


def _state(per_encoder: dict, doc_id: str = "doc-quorum") -> ExGraphState:
    return {
        "raw_text": "Sample document text.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "per_encoder_mentions": per_encoder,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# Default quorum = ceil(N/2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_5_encoders_mention_by_2_is_rejected():
    """5 encoders, mention found by 2 → below ceil(5/2)=3 → rejected."""
    encoders = ["a", "b", "c", "d", "e"]
    per_encoder = {
        "a": [_mention("Alice", "a")],
        "b": [_mention("Alice", "b")],
        "c": [],
        "d": [],
        "e": [],
    }

    node = ConsensusNode(encoders=encoders)
    assert node.default_quorum == math.ceil(5 / 2)  # == 3

    result = await node(_state(per_encoder))

    assert result["consensus_mentions"] == []
    assert len(result["rejected_mentions"]) == 1
    assert result["rejected_mentions"][0]["vote_count"] == 2
    assert result["rejected_mentions"][0]["quorum"] == 3
    assert result["rejected_mentions"][0]["reason"] == "below_quorum"


@pytest.mark.asyncio
async def test_5_encoders_mention_by_3_is_accepted():
    """5 encoders, mention found by exactly 3 → meets ceil(5/2)=3 → accepted."""
    encoders = ["a", "b", "c", "d", "e"]
    per_encoder = {
        "a": [_mention("Alice", "a")],
        "b": [_mention("Alice", "b")],
        "c": [_mention("Alice", "c")],
        "d": [],
        "e": [],
    }

    node = ConsensusNode(encoders=encoders)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    assert result["consensus_mentions"][0]["vote_count"] == 3


@pytest.mark.asyncio
async def test_4_encoders_default_quorum_is_2():
    """4 encoders → ceil(4/2) = 2."""
    encoders = ["a", "b", "c", "d"]
    node = ConsensusNode(encoders=encoders)
    assert node.default_quorum == 2


@pytest.mark.asyncio
async def test_1_encoder_default_quorum_is_1():
    """1 encoder → ceil(1/2) = 1."""
    node = ConsensusNode(encoders=["only"])
    assert node.default_quorum == 1


@pytest.mark.asyncio
async def test_explicit_quorum_override():
    """quorum=4 with 5 encoders: mention found by 3 → rejected."""
    encoders = ["a", "b", "c", "d", "e"]
    per_encoder = {
        "a": [_mention("Alice", "a")],
        "b": [_mention("Alice", "b")],
        "c": [_mention("Alice", "c")],
        "d": [],
        "e": [],
    }

    node = ConsensusNode(encoders=encoders, quorum=4)
    result = await node(_state(per_encoder))

    assert result["consensus_mentions"] == []
    assert result["rejected_mentions"][0]["quorum"] == 4


@pytest.mark.asyncio
async def test_multiple_mentions_only_some_pass_quorum():
    """Mixed scenario: 'Alice' passes quorum (3/5), 'Bob' doesn't (1/5)."""
    encoders = ["a", "b", "c", "d", "e"]
    per_encoder = {
        "a": [_mention("Alice", "a"), _mention("Bob", "a", span_start=20)],
        "b": [_mention("Alice", "b")],
        "c": [_mention("Alice", "c")],
        "d": [],
        "e": [],
    }

    node = ConsensusNode(encoders=encoders)  # default quorum=3
    result = await node(_state(per_encoder))

    accepted_texts = {m["text"] for m in result["consensus_mentions"]}
    rejected_texts = {m["text"] for m in result["rejected_mentions"]}

    assert "alice" in accepted_texts
    assert "bob" in rejected_texts
    assert len(result["consensus_mentions"]) == 1
    assert len(result["rejected_mentions"]) == 1


@pytest.mark.asyncio
async def test_rejected_mention_has_required_fields():
    """Rejected mention dict has: text, vote_count, n_encoders, quorum, reason."""
    encoders = ["a", "b", "c"]
    per_encoder = {
        "a": [_mention("Alice", "a")],
        "b": [],
        "c": [],
    }

    node = ConsensusNode(encoders=encoders)  # quorum=2
    result = await node(_state(per_encoder))

    assert len(result["rejected_mentions"]) == 1
    r = result["rejected_mentions"][0]
    assert "text" in r
    assert "vote_count" in r
    assert "n_encoders" in r
    assert "quorum" in r
    assert r["reason"] == "below_quorum"
