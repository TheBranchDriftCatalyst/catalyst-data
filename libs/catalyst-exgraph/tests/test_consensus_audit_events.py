"""Test ConsensusNode audit events — every decision must be audited.

Phase B / CD-94ow.  Verifies the full audit event contract:
- consensus_started at node entry
- mention_decision per accepted mention
- mention_rejected per rejected mention
- consensus_completed at node exit

No silent drops: every per-encoder mention must produce exactly one
mention_decision or mention_rejected event.
"""

from __future__ import annotations

import json
from pathlib import Path

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


def _state(per_encoder: dict, doc_id: str = "doc-audit") -> ExGraphState:
    return {
        "raw_text": "Alice met Bob in Paris.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "per_encoder_mentions": per_encoder,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


def _read_events(tmp_path: Path) -> list[dict]:
    """Read all events from the configured event_tail JSONL."""
    tail_path = tmp_path / "events.jsonl"
    if not tail_path.exists():
        return []
    return [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consensus_started_event_emitted(tmp_path):
    """consensus_started event is emitted with n_encoders and total_input_mentions."""
    encoders = ["gliner-medium", "gliner-large", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [_mention("Alice", "gliner-medium")],
        "gliner-large": [_mention("Alice", "gliner-large")],
        "universalner-7b": [],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    await node(_state(per_encoder))

    events = _read_events(tmp_path)
    started = [e for e in events if e["node_name"] == "consensus_started"]
    assert len(started) == 1
    d = started[0]["details"]
    assert d["n_encoders"] == 3
    assert d["total_input_mentions"] == 2


@pytest.mark.asyncio
async def test_mention_decision_event_per_accepted_mention(tmp_path):
    """Each accepted mention produces exactly one mention_decision event."""
    encoders = ["gliner-medium", "gliner-large", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [_mention("Alice", "gliner-medium"), _mention("Bob", "gliner-medium", span_start=10)],
        "gliner-large": [_mention("Alice", "gliner-large"), _mention("Bob", "gliner-large", span_start=10)],
        "universalner-7b": [_mention("Alice", "universalner-7b"), _mention("Bob", "universalner-7b", span_start=10)],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 2

    events = _read_events(tmp_path)
    decisions = [e for e in events if e["node_name"] == "mention_decision" and e["status"] == "accepted"]
    assert len(decisions) == 2


@pytest.mark.asyncio
async def test_mention_decision_event_has_required_fields(tmp_path):
    """mention_decision events carry all required provenance fields."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        "gliner-medium": [_mention("Alice", "gliner-medium")],
        "gliner-large": [_mention("Alice", "gliner-large")],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    await node(_state(per_encoder, doc_id="test-doc"))

    events = _read_events(tmp_path)
    decisions = [e for e in events if e["node_name"] == "mention_decision"]
    assert len(decisions) == 1

    d = decisions[0]["details"]
    required = {
        "text",
        "canonical_type",
        "vote_count",
        "n_encoders",
        "source_models",
        "mean_confidence",
        "type_votes",
        "span_provenance",
        "span_disagreement_chars",
    }
    for field in required:
        assert field in d, f"missing required field: {field}"

    # chunk_id must use the consensus pattern
    assert decisions[0]["chunk_id"] == "test-doc:_consensus"


@pytest.mark.asyncio
async def test_mention_rejected_event_per_rejected_mention(tmp_path):
    """Each rejected mention produces exactly one mention_rejected event."""
    encoders = ["a", "b", "c"]  # quorum=2
    per_encoder = {
        "a": [_mention("Alice", "a"), _mention("Bob", "a", span_start=20)],
        "b": [],
        "c": [],
    }

    node = ConsensusNode(encoders=encoders)  # ceil(3/2)=2
    result = await node(_state(per_encoder))

    assert len(result["rejected_mentions"]) == 2

    events = _read_events(tmp_path)
    rejections = [e for e in events if e["node_name"] == "mention_rejected"]
    assert len(rejections) == 2


@pytest.mark.asyncio
async def test_mention_rejected_event_has_required_fields(tmp_path):
    """mention_rejected events carry text, vote_count, n_encoders, quorum, reason."""
    encoders = ["a", "b", "c"]
    per_encoder = {
        "a": [_mention("Alice", "a")],
        "b": [],
        "c": [],
    }

    node = ConsensusNode(encoders=encoders)
    await node(_state(per_encoder))

    events = _read_events(tmp_path)
    rejections = [e for e in events if e["node_name"] == "mention_rejected"]
    assert len(rejections) == 1

    d = rejections[0]["details"]
    assert d["reason"] == "below_quorum"
    assert "vote_count" in d
    assert "n_encoders" in d
    assert "quorum" in d


@pytest.mark.asyncio
async def test_consensus_completed_event_emitted(tmp_path):
    """consensus_completed event is emitted with summary stats."""
    encoders = ["a", "b", "c"]
    per_encoder = {
        "a": [_mention("Alice", "a"), _mention("Bob", "a", span_start=10)],
        "b": [_mention("Alice", "b")],
        "c": [],
    }

    node = ConsensusNode(encoders=encoders)  # quorum=2
    await node(_state(per_encoder))

    events = _read_events(tmp_path)
    completed = [e for e in events if e["node_name"] == "consensus_completed"]
    assert len(completed) == 1

    d = completed[0]["details"]
    assert "accepted_count" in d
    assert "rejected_count" in d
    assert "mean_vote_count" in d
    assert "type_distribution" in d
    assert "span_disagreement_rate" in d

    # accepted + rejected = total unique clusters
    total_clusters = d["accepted_count"] + d["rejected_count"]
    assert total_clusters >= 1


@pytest.mark.asyncio
async def test_empty_input_still_emits_started_and_completed(tmp_path):
    """Even with no mentions, consensus_started and consensus_completed are emitted."""
    node = ConsensusNode(encoders=["a", "b", "c"])
    await node(_state({}))

    events = _read_events(tmp_path)
    node_names = [e["node_name"] for e in events]
    assert "consensus_started" in node_names
    assert "consensus_completed" in node_names


@pytest.mark.asyncio
async def test_event_order_is_started_decisions_completed(tmp_path):
    """Events should appear in order: started → decision/rejected events → completed."""
    encoders = ["a", "b", "c"]
    per_encoder = {
        "a": [_mention("Alice", "a")],
        "b": [_mention("Alice", "b")],
        "c": [_mention("Bob", "c", span_start=10)],
    }

    node = ConsensusNode(encoders=encoders)  # quorum=2
    await node(_state(per_encoder))

    events = _read_events(tmp_path)
    consensus_events = [
        e
        for e in events
        if e["node_name"] in {"consensus_started", "mention_decision", "mention_rejected", "consensus_completed"}
    ]

    assert consensus_events[0]["node_name"] == "consensus_started"
    assert consensus_events[-1]["node_name"] == "consensus_completed"


@pytest.mark.asyncio
async def test_chunk_id_uses_consensus_pattern(tmp_path):
    """All consensus events use chunk_id = '{doc_id}:_consensus'."""
    encoders = ["a", "b"]
    per_encoder = {
        "a": [_mention("Alice", "a")],
        "b": [_mention("Alice", "b")],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    await node(_state(per_encoder, doc_id="my-doc-42"))

    events = _read_events(tmp_path)
    consensus_events = [
        e
        for e in events
        if e["node_name"] in {"consensus_started", "mention_decision", "mention_rejected", "consensus_completed"}
    ]

    for e in consensus_events:
        assert e["chunk_id"] == "my-doc-42:_consensus", f"event {e['node_name']} has wrong chunk_id: {e['chunk_id']}"
