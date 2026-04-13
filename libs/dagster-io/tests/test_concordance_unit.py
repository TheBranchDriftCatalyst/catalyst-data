"""Layer 1: Unit tests for concordance helper functions and data structures.

Fast, isolated tests — one function at a time. No engine composition.
"""

from __future__ import annotations

import math

import pytest
from concordance_helpers import make_embedding, make_similar_embedding

from dagster_io.concordance import _cosine_similarity, _jaccard, _tokenize, _UnionFind

# ── _tokenize ───────────────────────────────────────────────────────────────


def test_tokenize_basic():
    assert _tokenize("Donald Trump") == {"donald", "trump"}


def test_tokenize_extra_whitespace():
    assert _tokenize("  Donald   Trump  ") == {"donald", "trump"}


def test_tokenize_empty():
    assert _tokenize("") == set()


# ── _jaccard ────────────────────────────────────────────────────────────────


def test_jaccard_identical():
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint():
    assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial():
    # {a,b,c} ∩ {b,c,d} = {b,c}, union = {a,b,c,d} → 2/4 = 0.5
    assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == 0.5


def test_jaccard_empty_set():
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard(set(), set()) == 0.0


# ── _cosine_similarity ─────────────────────────────────────────────────────


def test_cosine_identical():
    assert _cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal():
    assert _cosine_similarity([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == pytest.approx(0.0)


def test_cosine_opposite():
    assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_empty():
    assert _cosine_similarity([], []) == 0.0


def test_cosine_mismatched_length():
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


# ── _UnionFind ──────────────────────────────────────────────────────────────


def test_unionfind_singleton():
    uf = _UnionFind()
    assert uf.find("a") == "a"
    clusters = uf.clusters()
    assert len(clusters) == 1
    assert "a" in list(clusters.values())[0]


def test_unionfind_two_unions():
    uf = _UnionFind()
    for x in "abcd":
        uf.find(x)
    uf.union("a", "b")
    uf.union("c", "d")
    clusters = uf.clusters()
    assert len(clusters) == 2


def test_unionfind_transitive_closure():
    """Union (a,b), (b,c), (c,d) → all in one cluster.

    This is the fundamental property that causes the Trump/Rumsfeld
    bug when guards are missing: weak individual merges compose
    transitively into false mega-clusters.
    """
    uf = _UnionFind()
    for x in "abcd":
        uf.find(x)
    uf.union("a", "b")
    uf.union("b", "c")
    uf.union("c", "d")
    clusters = uf.clusters()
    assert len(clusters) == 1
    assert len(list(clusters.values())[0]) == 4


def test_unionfind_idempotent():
    uf = _UnionFind()
    uf.find("a")
    uf.find("b")
    uf.union("a", "b")
    uf.union("a", "b")  # repeat
    clusters = uf.clusters()
    assert len(clusters) == 1
    assert len(list(clusters.values())[0]) == 2


# ── Embedding helpers ───────────────────────────────────────────────────────


def test_make_embedding_unit_normalized():
    emb = make_embedding(seed=42, dim=64)
    norm = math.sqrt(sum(x * x for x in emb))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_make_similar_embedding_cosine_accuracy():
    """Verify make_similar_embedding produces the requested cosine similarity."""
    base = make_embedding(seed=1, dim=64)
    for target_sim in [0.50, 0.70, 0.85, 0.87, 0.92, 0.99]:
        similar = make_similar_embedding(base, target_sim)
        actual_cos = _cosine_similarity(base, similar)
        assert actual_cos == pytest.approx(target_sim, abs=0.01), f"Requested cosine {target_sim}, got {actual_cos}"
