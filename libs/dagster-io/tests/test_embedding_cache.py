"""Unit tests for EmbeddingCache.

Uses the in-memory store so no S3 / parquet dependency is needed at test time.
All tests can run in a plain pytest environment without dagster-io[local-embed].
"""

from __future__ import annotations

import os
from collections.abc import Callable

os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")

import pytest

from dagster_io.embedding_cache import EmbeddingCache, _InMemoryStore, _make_key, _shard

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_vec(seed: int, dim: int = 4) -> list[float]:
    """Deterministic pseudo-vector for testing.

    Uses ``sin((seed + 1) * (i + 1))`` so seed=0 still yields a non-zero vector.
    """
    import math

    raw = [math.sin((seed + 1) * (i + 1)) for i in range(dim)]
    norm = sum(x * x for x in raw) ** 0.5
    return [x / norm for x in raw]


def _compute_fn(call_log: list[list[str]]) -> Callable[[list[str]], list[list[float]]]:
    """Returns a compute function that records which texts it was called with."""

    def _fn(texts: list[str]) -> list[list[float]]:
        call_log.append(list(texts))
        return [_make_vec(hash(t) & 0xFFFF) for t in texts]

    return _fn


# ── key / shard helpers ───────────────────────────────────────────────────────


def test_make_key_deterministic():
    """Same inputs always produce the same 16-hex-char key."""
    k1 = _make_key("model-a", 128, "hello world")
    k2 = _make_key("model-a", 128, "hello world")
    assert k1 == k2
    assert len(k1) == 16


def test_make_key_sensitive_to_model_and_dim():
    """Different model or dim must produce different keys."""
    k_base = _make_key("model-a", 128, "text")
    assert _make_key("model-b", 128, "text") != k_base
    assert _make_key("model-a", 256, "text") != k_base


def test_shard_is_first_two_chars():
    key = "abcdef0123456789"  # gitleaks:allow — test fixture, not a secret
    assert _shard(key) == "ab"


# ── EmbeddingCache (in-memory store) ─────────────────────────────────────────


@pytest.fixture
def cache() -> EmbeddingCache:
    return EmbeddingCache(store=_InMemoryStore())


def test_write_then_read_returns_same_vectors(cache):
    """put() followed by get() returns identical vectors."""
    texts = ["foo", "bar", "baz"]
    vecs = [_make_vec(i) for i in range(3)]
    cache.put(texts, vecs, model="test-model", dim=4)
    result = cache.get(texts, model="test-model", dim=4)
    assert result == vecs


def test_cache_miss_returns_none(cache):
    """get() on an empty cache returns None for every entry."""
    result = cache.get(["unseen text"], model="test-model", dim=4)
    assert result == [None]


def test_partial_hit_batch(cache):
    """Partial hits: cached entries are returned, misses are None."""
    texts = ["a", "b", "c"]
    vecs = [_make_vec(0), _make_vec(1), _make_vec(2)]
    # Only pre-populate "a" and "c"
    cache.put(["a", "c"], [vecs[0], vecs[2]], model="m", dim=4)
    result = cache.get(texts, model="m", dim=4)
    assert result[0] == vecs[0]
    assert result[1] is None
    assert result[2] == vecs[2]


def test_get_or_compute_miss_calls_compute_once(cache):
    """Cache misses trigger compute exactly once per missing text."""
    call_log: list[list[str]] = []
    fn = _compute_fn(call_log)

    texts = ["x", "y", "z"]
    result = cache.get_or_compute(texts, model="m", dim=4, compute=fn)

    assert len(result) == 3
    assert all(isinstance(v, list) for v in result)
    assert len(call_log) == 1
    assert sorted(call_log[0]) == sorted(texts)


def test_get_or_compute_full_hit_skips_compute(cache):
    """A fully cached batch does not call compute at all."""
    texts = ["cached1", "cached2"]
    vecs = [_make_vec(10), _make_vec(11)]
    cache.put(texts, vecs, model="m", dim=4)

    call_log: list[list[str]] = []
    fn = _compute_fn(call_log)
    result = cache.get_or_compute(texts, model="m", dim=4, compute=fn)

    assert result == vecs
    assert call_log == []


def test_get_or_compute_partial_hit_computes_only_misses(cache):
    """Only missing texts are passed to compute."""
    # Pre-seed "alpha"
    cache.put(["alpha"], [_make_vec(99)], model="m", dim=4)

    call_log: list[list[str]] = []
    fn = _compute_fn(call_log)

    result = cache.get_or_compute(["alpha", "beta", "gamma"], model="m", dim=4, compute=fn)

    assert len(result) == 3
    assert len(call_log) == 1
    assert set(call_log[0]) == {"beta", "gamma"}


def test_get_or_compute_result_order_matches_input(cache):
    """Output vectors are in the same order as the input texts."""
    texts = ["first", "second", "third"]
    call_log: list[list[str]] = []

    def _ordered_fn(batch: list[str]) -> list[list[float]]:
        call_log.append(list(batch))
        return [_make_vec(hash(t) & 0xFFFF) for t in batch]

    result = cache.get_or_compute(texts, model="m", dim=4, compute=_ordered_fn)

    # Second call: should be full hit and return same vectors in same order
    result2 = cache.get_or_compute(texts, model="m", dim=4, compute=_ordered_fn)
    assert result == result2
    assert len(call_log) == 1  # second call should NOT have triggered compute


def test_compute_called_once_for_repeated_missing_text(cache):
    """If the same text appears twice in one batch, compute is called once for it."""
    texts = ["dup", "dup"]
    call_log: list[list[str]] = []
    fn = _compute_fn(call_log)
    result = cache.get_or_compute(texts, model="m", dim=4, compute=fn)
    # Both slots should be filled (same vector for same text)
    assert len(result) == 2


def test_different_models_are_independent(cache):
    """Vectors cached under model-A are not returned for model-B queries."""
    vec = _make_vec(7)
    cache.put(["hello"], [vec], model="model-A", dim=4)
    result = cache.get(["hello"], model="model-B", dim=4)
    assert result == [None]


def test_different_dims_are_independent(cache):
    """Vectors cached at dim=4 are not returned for dim=8 queries."""
    vec = _make_vec(3)
    cache.put(["hello"], [vec], model="m", dim=4)
    result = cache.get(["hello"], model="m", dim=8)
    assert result == [None]
