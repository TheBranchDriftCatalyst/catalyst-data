"""Test ConsensusNode clustering — identical (text, type) across encoders → 1 cluster.

Phase B / CD-94ow.  No GPU or real encoders required.
"""

from __future__ import annotations

import pytest
from catalyst_exgraph.nodes.consensus import ConsensusNode
from catalyst_exgraph.state import ExGraphState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mention(text: str, mention_type: str, encoder: str, span_start: int = 0, confidence: float = 0.9) -> dict:
    span_end = span_start + len(text)
    return {
        "text": text,
        "mention_type": mention_type,
        "span_start": span_start,
        "span_end": span_end,
        "confidence": confidence,
        "_source_encoder": encoder,
    }


def _state(per_encoder: dict, doc_id: str = "doc-test") -> ExGraphState:
    return {
        "raw_text": "Alice met Bob at Acme Corp.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "per_encoder_mentions": per_encoder,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_identical_mentions_collapse_to_one():
    """Same text + type from 3 encoders → 1 consensus mention, vote_count=3."""
    encoders = ["gliner-medium", "gliner-large", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [_mention("Alice", "PERSON", "gliner-medium", span_start=0)],
        "gliner-large": [_mention("Alice", "PERSON", "gliner-large", span_start=0)],
        "universalner-7b": [_mention("Alice", "PERSON", "universalner-7b", span_start=0)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    m = result["consensus_mentions"][0]
    assert m["text"] == "alice"
    assert m["canonical_type"] == "PERSON"
    assert m["vote_count"] == 3
    assert set(m["source_models"]) == {"gliner-medium", "gliner-large", "universalner-7b"}


@pytest.mark.asyncio
async def test_vote_count_equals_unique_encoders_not_total_mentions():
    """If one encoder emits the same mention twice, vote_count still = 1 for that encoder."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        # gliner-medium emits "Alice" twice (duplicate)
        "gliner-medium": [
            _mention("Alice", "PERSON", "gliner-medium", span_start=0, confidence=0.9),
            _mention("Alice", "PERSON", "gliner-medium", span_start=0, confidence=0.8),
        ],
        "gliner-large": [_mention("Alice", "PERSON", "gliner-large", span_start=0)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    m = result["consensus_mentions"][0]
    # vote_count = unique encoders = 2 (both contributed)
    assert m["vote_count"] == 2


@pytest.mark.asyncio
async def test_different_texts_produce_separate_clusters():
    """'Alice' and 'Bob' from same encoder → 2 separate consensus mentions."""
    encoders = ["gliner-medium", "gliner-large", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [
            _mention("Alice", "PERSON", "gliner-medium", span_start=0),
            _mention("Bob", "PERSON", "gliner-medium", span_start=10),
        ],
        "gliner-large": [
            _mention("Alice", "PERSON", "gliner-large", span_start=0),
            _mention("Bob", "PERSON", "gliner-large", span_start=10),
        ],
        "universalner-7b": [
            _mention("Alice", "PERSON", "universalner-7b", span_start=0),
            _mention("Bob", "PERSON", "universalner-7b", span_start=10),
        ],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    texts = {m["text"] for m in result["consensus_mentions"]}
    assert texts == {"alice", "bob"}
    assert len(result["consensus_mentions"]) == 2


@pytest.mark.asyncio
async def test_canonical_text_is_lowercased_and_stripped():
    """Mentions with whitespace-padded or mixed-case text still cluster correctly."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        "gliner-medium": [_mention("  Alice  ", "PERSON", "gliner-medium", span_start=0)],
        "gliner-large": [_mention("ALICE", "PERSON", "gliner-large", span_start=0)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    assert result["consensus_mentions"][0]["vote_count"] == 2


@pytest.mark.asyncio
async def test_empty_per_encoder_returns_empty_lists():
    """No mentions → empty accepted and rejected."""
    node = ConsensusNode(encoders=["a", "b", "c"], quorum=2)
    result = await node(_state({}))

    assert result["consensus_mentions"] == []
    assert result["rejected_mentions"] == []


@pytest.mark.asyncio
async def test_mention_id_is_stable_across_calls():
    """Same input → same mention_id on repeated invocations."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        "gliner-medium": [_mention("Alice", "PERSON", "gliner-medium", span_start=5)],
        "gliner-large": [_mention("Alice", "PERSON", "gliner-large", span_start=5)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    r1 = await node(_state(per_encoder, doc_id="doc-a"))
    r2 = await node(_state(per_encoder, doc_id="doc-b"))  # different doc, same text

    # mention_id is derived from (text, type, span_start) — same across docs
    assert r1["consensus_mentions"][0]["mention_id"] == r2["consensus_mentions"][0]["mention_id"]
