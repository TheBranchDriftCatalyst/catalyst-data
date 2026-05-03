"""Tests for ClusterCache — write-then-read, cache miss on key change, idempotent put.

Phase 3 (CD-80ic). CD-jsha: extended for CachedNerResult shape (clusters + mentions).
"""

import json

from dagster_io.cluster_cache import CachedNerResult, ClusterCache, _InMemoryStore, _make_cluster_key


def _make_cluster(idx: int) -> dict:
    return {
        "cluster_id": f"cl-{idx}",
        "mention_indices": [idx],
        "doc_char_start": idx * 10,
        "doc_char_end": idx * 10 + 5,
    }


def _make_mention(idx: int) -> dict:
    return {
        "text": f"Entity{idx}",
        "label": "PERSON",
        "start": idx * 20,
        "end": idx * 20 + 8,
        "score": 0.95,
    }


class TestClusterCache:
    def _cache(self) -> ClusterCache:
        """Return a ClusterCache backed by an in-memory store (no S3)."""
        return ClusterCache(store=_InMemoryStore(), code_location="test")

    # ── Basic write-then-read ───────────────────────────────────────────────

    def test_put_then_get_returns_clusters(self):
        cache = self._cache()
        result = CachedNerResult(clusters=[_make_cluster(0), _make_cluster(1)], mentions=[])
        params = {"threshold": 0.85, "proximity_radius": 512}

        cache.put("doc-1", "hello world", "gliner-large", params, result)
        got = cache.get("doc-1", "hello world", "gliner-large", params)

        assert got is not None
        assert len(got.clusters) == 2
        assert got.clusters[0]["cluster_id"] == "cl-0"
        assert got.clusters[1]["cluster_id"] == "cl-1"

    def test_miss_returns_none(self):
        cache = self._cache()
        result = cache.get("doc-99", "some text", "gliner-large", {})
        assert result is None

    # ── Content key change → cache miss ────────────────────────────────────

    def test_different_doc_text_is_a_miss(self):
        cache = self._cache()
        result = CachedNerResult(clusters=[_make_cluster(0)], mentions=[])
        cache.put("doc-1", "text A", "gliner-large", {}, result)

        # Different doc text → different cache key → miss
        got = cache.get("doc-1", "text B", "gliner-large", {})
        assert got is None

    def test_different_ner_model_is_a_miss(self):
        cache = self._cache()
        result = CachedNerResult(clusters=[_make_cluster(0)], mentions=[])
        cache.put("doc-1", "text A", "gliner-large", {}, result)

        got = cache.get("doc-1", "text A", "gliner-medium", {})
        assert got is None

    def test_different_params_is_a_miss(self):
        cache = self._cache()
        result = CachedNerResult(clusters=[_make_cluster(0)], mentions=[])
        cache.put("doc-1", "text A", "gliner-large", {"threshold": 0.85}, result)

        got = cache.get("doc-1", "text A", "gliner-large", {"threshold": 0.90})
        assert got is None

    # ── Idempotent put ──────────────────────────────────────────────────────

    def test_put_twice_same_key_is_idempotent(self):
        cache = self._cache()
        result = CachedNerResult(clusters=[_make_cluster(0)], mentions=[])
        params: dict = {}

        cache.put("doc-1", "text A", "gliner-large", params, result)
        cache.put("doc-1", "text A", "gliner-large", params, result)

        got = cache.get("doc-1", "text A", "gliner-large", params)
        assert got is not None
        assert len(got.clusters) == 1

    # ── get_or_compute ──────────────────────────────────────────────────────

    def test_get_or_compute_calls_fn_on_miss(self):
        cache = self._cache()
        calls = []

        def compute():
            calls.append(1)
            return CachedNerResult(clusters=[_make_cluster(0)], mentions=[])

        got = cache.get_or_compute("doc-1", "text", "gliner-large", {}, compute)
        assert len(got.clusters) == 1
        assert len(calls) == 1, "compute_fn should be called exactly once on a miss"

    def test_get_or_compute_skips_fn_on_hit(self):
        cache = self._cache()
        original = CachedNerResult(clusters=[_make_cluster(0)], mentions=[])
        cache.put("doc-1", "text", "gliner-large", {}, original)

        calls = []

        def compute():
            calls.append(1)
            return CachedNerResult(clusters=[_make_cluster(99)], mentions=[])

        got = cache.get_or_compute("doc-1", "text", "gliner-large", {}, compute)
        assert len(calls) == 0, "compute_fn should NOT be called on a cache hit"
        assert got.clusters[0]["cluster_id"] == "cl-0"  # original cached value

    def test_second_get_or_compute_is_fast(self):
        """Second invocation should return immediately without calling compute_fn."""
        import time

        cache = self._cache()

        def slow_compute():
            time.sleep(0.05)
            return CachedNerResult(clusters=[_make_cluster(0)], mentions=[])

        # First call — must compute
        cache.get_or_compute("doc-1", "text", "gliner-large", {}, slow_compute)

        # Second call — should hit cache, not sleep
        t0 = time.perf_counter()
        got = cache.get_or_compute("doc-1", "text", "gliner-large", {}, slow_compute)
        elapsed = time.perf_counter() - t0

        assert got is not None
        # Cache hit should be << 50ms (the sleep time)
        assert elapsed < 0.01, f"Expected < 10ms for cache hit, got {elapsed * 1000:.1f}ms"

    # ── Cache key determinism ───────────────────────────────────────────────

    def test_cache_key_is_deterministic(self):
        key1 = _make_cluster_key("text A", "gliner-large", {"threshold": 0.85})
        key2 = _make_cluster_key("text A", "gliner-large", {"threshold": 0.85})
        assert key1 == key2

    def test_cache_key_differs_on_text(self):
        key1 = _make_cluster_key("text A", "gliner-large", {})
        key2 = _make_cluster_key("text B", "gliner-large", {})
        assert key1 != key2

    # ── Empty clusters ──────────────────────────────────────────────────────

    def test_put_empty_clusters_stored_and_retrieved(self):
        cache = self._cache()
        result = CachedNerResult(clusters=[], mentions=[])
        cache.put("doc-1", "text", "gliner-large", {}, result)
        got = cache.get("doc-1", "text", "gliner-large", {})
        # Should return a result with empty lists, not None (miss)
        assert got is not None
        assert got.clusters == []
        assert got.mentions == []

    # ── CD-jsha: mentions round-trip ────────────────────────────────────────

    def test_round_trip_with_mentions(self):
        """put → get returns the same clusters AND mentions."""
        cache = self._cache()
        clusters = [_make_cluster(0), _make_cluster(1)]
        mentions = [_make_mention(0), _make_mention(1), _make_mention(2)]
        result = CachedNerResult(clusters=clusters, mentions=mentions)
        params = {"threshold": 0.85, "proximity_radius": 512}

        cache.put("doc-1", "some document text", "gliner-large", params, result)
        got = cache.get("doc-1", "some document text", "gliner-large", params)

        assert got is not None
        assert len(got.clusters) == 2
        assert got.clusters[0]["cluster_id"] == "cl-0"
        assert got.clusters[1]["cluster_id"] == "cl-1"
        assert len(got.mentions) == 3
        assert got.mentions[0]["text"] == "Entity0"
        assert got.mentions[2]["label"] == "PERSON"

    def test_legacy_shape_read_compat(self):
        """An old-shape cache entry (bare list) must not crash.

        get() must return a CachedNerResult with mentions=[] (graceful
        degradation) rather than raising an exception.
        """
        store = _InMemoryStore()
        cache = ClusterCache(store=store, code_location="test")

        # Plant a legacy-shaped entry directly — a bare JSON list of clusters
        legacy_clusters = [_make_cluster(0), _make_cluster(1)]
        legacy_bytes = json.dumps(legacy_clusters).encode("utf-8")

        key = _make_cluster_key("legacy text", "gliner-large", {})
        store.write_raw("test", key, legacy_bytes)

        got = cache.get("doc-legacy", "legacy text", "gliner-large", {})

        assert got is not None, "Legacy entry should not return None (should not be a miss)"
        assert len(got.clusters) == 2
        assert got.clusters[0]["cluster_id"] == "cl-0"
        assert got.mentions == [], "Legacy entry must degrade to empty mentions, not crash"

    def test_get_or_compute_with_mentions(self):
        """Cold call populates clusters + mentions; warm call returns the same."""
        cache = self._cache()
        clusters = [_make_cluster(0)]
        mentions = [_make_mention(0)]
        calls = []

        def compute():
            calls.append(1)
            return CachedNerResult(clusters=clusters, mentions=mentions)

        # Cold call — compute_fn must run
        cold = cache.get_or_compute("doc-1", "document text", "gliner-large", {}, compute)
        assert len(calls) == 1
        assert len(cold.clusters) == 1
        assert len(cold.mentions) == 1
        assert cold.mentions[0]["text"] == "Entity0"

        # Warm call — compute_fn must NOT run; result must be identical
        warm = cache.get_or_compute("doc-1", "document text", "gliner-large", {}, compute)
        assert len(calls) == 1, "compute_fn must not fire on warm hit"
        assert len(warm.clusters) == 1
        assert len(warm.mentions) == 1
        assert warm.clusters[0]["cluster_id"] == cold.clusters[0]["cluster_id"]
        assert warm.mentions[0]["text"] == cold.mentions[0]["text"]
