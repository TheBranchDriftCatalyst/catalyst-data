"""Tests for ClusterEntitiesNode — proximity-pass clustering (CD-j6d3).

These tests do NOT require Qwen3-8B to be downloaded; the embedder is not
injected so only proximity clustering runs.
"""

from __future__ import annotations

import pytest
from catalyst_exgraph.nodes.cluster import ClusterEntitiesNode, _proximity_cluster

# ── Unit tests for the proximity helper ─────────────────────────────────────


def _make_mention(text: str, start: int, end: int) -> dict:
    return {"text": text, "span_start": start, "span_end": end}


def test_proximity_cluster_two_groups():
    """Entities at chars 100, 150, 800 → two clusters: {100,150} and {800}."""
    mentions = [
        _make_mention("Alice", 100, 105),
        _make_mention("Bob", 150, 153),
        _make_mention("Carol", 800, 805),
    ]
    clusters = _proximity_cluster(mentions, proximity_radius=200)
    assert len(clusters) == 2, f"Expected 2 clusters, got {len(clusters)}: {clusters}"

    # Flatten indices
    flat = [sorted(c) for c in clusters]
    # Cluster containing indices 0 and 1 (Alice + Bob)
    assert [0, 1] in flat or flat[0] == [0, 1] or flat[1] == [0, 1]
    # Cluster containing index 2 (Carol)
    assert any(c == [2] for c in flat)


def test_proximity_cluster_all_in_one():
    """All entities within proximity → single cluster."""
    mentions = [
        _make_mention("X", 0, 1),
        _make_mention("Y", 50, 51),
        _make_mention("Z", 100, 101),
    ]
    clusters = _proximity_cluster(mentions, proximity_radius=200)
    assert len(clusters) == 1
    assert sorted(clusters[0]) == [0, 1, 2]


def test_proximity_cluster_all_separate():
    """All entities far apart → N singleton clusters."""
    mentions = [
        _make_mention("A", 0, 1),
        _make_mention("B", 500, 501),
        _make_mention("C", 1000, 1001),
    ]
    clusters = _proximity_cluster(mentions, proximity_radius=100)
    assert len(clusters) == 3
    for c in clusters:
        assert len(c) == 1


def test_proximity_cluster_empty():
    """Empty mention list → empty cluster list."""
    assert _proximity_cluster([], 200) == []


def test_proximity_cluster_single():
    """Single mention → one singleton cluster."""
    clusters = _proximity_cluster([_make_mention("A", 10, 15)], 200)
    assert len(clusters) == 1
    assert clusters[0] == [0]


# ── Integration test: ClusterEntitiesNode (no embedder) ─────────────────────


@pytest.mark.asyncio
async def test_cluster_node_two_clusters():
    """ClusterEntitiesNode without embedder: proximity-only → two clusters."""
    node = ClusterEntitiesNode(embedder=None, proximity_radius=200)

    state = {
        "raw_text": "A" * 1000,
        "stages": {
            "ner": {
                "accepted": [
                    _make_mention("Alice", 100, 105),
                    _make_mention("Bob", 150, 153),
                    _make_mention("Carol", 800, 805),
                ]
            }
        },
        "audit_events": [],
    }
    result = await node(state)

    clusters = result["entity_clusters"]
    assert len(clusters) == 2, f"Expected 2 clusters, got {len(clusters)}"

    # Each cluster must have cluster_id, mention_indices, bounding box
    for c in clusters:
        assert "cluster_id" in c
        assert "mention_indices" in c
        assert "doc_char_start" in c
        assert "doc_char_end" in c

    # Verify bounding boxes are non-zero
    assert any(c["doc_char_start"] >= 100 and c["doc_char_end"] >= 105 for c in clusters)

    # Audit event emitted
    audit = result.get("audit_events", [])
    assert audit, "Expected at least one audit event"
    cluster_event = next((e for e in audit if e.get("node_name") == "cluster_entities"), None)
    assert cluster_event is not None
    assert cluster_event["details"]["pre_merge_count"] == 2
    assert cluster_event["details"]["post_merge_count"] == 2


@pytest.mark.asyncio
async def test_cluster_node_no_mentions():
    """ClusterEntitiesNode with no NER mentions → empty clusters."""
    node = ClusterEntitiesNode(embedder=None)

    state = {
        "raw_text": "Some text",
        "stages": {"ner": {"accepted": []}},
        "audit_events": [],
    }
    result = await node(state)
    assert result["entity_clusters"] == []
    assert result["audit_events"]


@pytest.mark.asyncio
async def test_cluster_node_uses_doc_char_start():
    """ClusterEntitiesNode prefers doc_char_start over span_start."""
    node = ClusterEntitiesNode(embedder=None, proximity_radius=50)

    state = {
        "raw_text": "A" * 500,
        "stages": {
            "ner": {
                "accepted": [
                    {
                        "text": "X",
                        "doc_char_start": 10,
                        "doc_char_end": 11,
                        "span_start": 999,  # should be ignored
                        "span_end": 1000,
                    },
                    {
                        "text": "Y",
                        "doc_char_start": 400,
                        "doc_char_end": 401,
                        "span_start": 999,
                        "span_end": 1000,
                    },
                ]
            }
        },
        "audit_events": [],
    }
    result = await node(state)
    # 400 - 11 = 389 > 50, so two separate clusters
    assert len(result["entity_clusters"]) == 2
