"""Tests for ClusterEntitiesNode — embedding-merge step (CD-j6d3).

Uses a mock embedder with controllable cosine values.
Qwen3-8B model is NOT required for these tests.
"""

from __future__ import annotations

import math

import pytest
from catalyst_exgraph.nodes.cluster import ClusterEntitiesNode, _cosine

# ── Helper: build a mock embedder ────────────────────────────────────────────


class _MockEmbedder:
    """Embedder that returns pre-canned vectors of unit length."""

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self._call_count = 0

    @property
    def model_name(self) -> str:
        return "mock-embedder"

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._call_count += 1
        # Return vectors in call order, cycling if more texts than vectors
        return [self._vectors[i % len(self._vectors)] for i in range(len(texts))]


def _unit(v: list[float]) -> list[float]:
    """Normalise vector to unit length."""
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm > 0 else v


def _make_mention(text: str, start: int, end: int) -> dict:
    return {"text": text, "span_start": start, "span_end": end}


# ── Unit test: cosine helper ──────────────────────────────────────────────────


def test_cosine_identical():
    v = _unit([1.0, 0.0, 0.0])
    assert abs(_cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal():
    a = _unit([1.0, 0.0])
    b = _unit([0.0, 1.0])
    assert abs(_cosine(a, b)) < 1e-6


def test_cosine_zero_vector():
    """Zero vector → cosine = 0, no division error."""
    a = [0.0, 0.0]
    b = _unit([1.0, 0.0])
    assert _cosine(a, b) == 0.0


# ── Integration: embedding merge ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embedding_merge_clusters_with_shared_entity():
    """Two proximity clusters with shared entity + high cosine → merged."""
    # Two clusters: {Alice at 100} and {Alice at 800} — same surface form
    # Embedder returns identical vectors → cosine = 1.0 > 0.75
    v = _unit([1.0, 0.0, 0.0, 0.0])
    embedder = _MockEmbedder([v, v])

    # Provide a mock EmbeddingCache that computes directly (no S3)
    from dagster_io.embedding_cache import EmbeddingCache, _InMemoryStore

    cache = EmbeddingCache(store=_InMemoryStore())

    node = ClusterEntitiesNode(
        embedder=embedder,
        cache=cache,
        proximity_radius=50,  # gap of 700 > 50 → two proximity clusters
        embed_merge_threshold=0.75,
    )

    raw_text = "A" * 1000
    state = {
        "raw_text": raw_text,
        "stages": {
            "ner": {
                "accepted": [
                    _make_mention("Alice", 100, 105),
                    _make_mention("Alice", 800, 805),  # same surface form!
                ]
            }
        },
        "audit_events": [],
    }
    result = await node(state)
    clusters = result["entity_clusters"]

    # Both clusters share "alice" and cosine ≥ 0.75 → should merge to 1
    assert len(clusters) == 1, (
        f"Expected 1 merged cluster, got {len(clusters)}: {[c['mention_indices'] for c in clusters]}"
    )
    assert sorted(clusters[0]["mention_indices"]) == [0, 1]

    # Audit event should show pre_merge=2, post_merge=1
    audit = result.get("audit_events", [])
    cluster_event = next(e for e in audit if e.get("node_name") == "cluster_entities")
    assert cluster_event["details"]["pre_merge_count"] == 2
    assert cluster_event["details"]["post_merge_count"] == 1


@pytest.mark.asyncio
async def test_embedding_merge_no_shared_entity_prevents_merge():
    """High cosine but NO shared entity surface form → clusters NOT merged."""
    v = _unit([1.0, 0.0, 0.0, 0.0])
    embedder = _MockEmbedder([v, v])

    from dagster_io.embedding_cache import EmbeddingCache, _InMemoryStore

    cache = EmbeddingCache(store=_InMemoryStore())

    node = ClusterEntitiesNode(
        embedder=embedder,
        cache=cache,
        proximity_radius=50,
        embed_merge_threshold=0.75,
    )

    state = {
        "raw_text": "A" * 1000,
        "stages": {
            "ner": {
                "accepted": [
                    _make_mention("Alice", 100, 105),  # cluster A
                    _make_mention("Bob", 800, 803),  # cluster B — different surface form
                ]
            }
        },
        "audit_events": [],
    }
    result = await node(state)
    # No shared entity surface form → two clusters remain despite high cosine
    assert len(result["entity_clusters"]) == 2


@pytest.mark.asyncio
async def test_embedding_merge_low_cosine_prevents_merge():
    """Shared entity but low cosine → clusters NOT merged."""
    a = _unit([1.0, 0.0, 0.0, 0.0])
    b = _unit([0.0, 1.0, 0.0, 0.0])  # orthogonal → cosine = 0
    embedder = _MockEmbedder([a, b])

    from dagster_io.embedding_cache import EmbeddingCache, _InMemoryStore

    cache = EmbeddingCache(store=_InMemoryStore())

    node = ClusterEntitiesNode(
        embedder=embedder,
        cache=cache,
        proximity_radius=50,
        embed_merge_threshold=0.75,
    )

    state = {
        "raw_text": "A" * 1000,
        "stages": {
            "ner": {
                "accepted": [
                    _make_mention("Alice", 100, 105),
                    _make_mention("Alice", 800, 805),  # shared entity, but cosine = 0
                ]
            }
        },
        "audit_events": [],
    }
    result = await node(state)
    assert len(result["entity_clusters"]) == 2


@pytest.mark.asyncio
async def test_embedding_uses_cache():
    """Second invocation with same texts should hit cache (no second embed call)."""
    v = _unit([1.0, 0.0])
    embedder = _MockEmbedder([v])

    from dagster_io.embedding_cache import EmbeddingCache, _InMemoryStore

    cache = EmbeddingCache(store=_InMemoryStore())

    node = ClusterEntitiesNode(embedder=embedder, cache=cache, proximity_radius=50)

    state = {
        "raw_text": "Hello World " * 100,
        "stages": {
            "ner": {
                "accepted": [
                    _make_mention("Alice", 0, 5),
                    _make_mention("Alice", 500, 505),
                ]
            }
        },
        "audit_events": [],
    }
    await node(state)
    first_call_count = embedder._call_count

    # Second invocation — cache should serve the vectors
    await node(state)
    assert embedder._call_count == first_call_count, (
        f"Expected no new embed calls on second invocation, got {embedder._call_count - first_call_count} more"
    )
