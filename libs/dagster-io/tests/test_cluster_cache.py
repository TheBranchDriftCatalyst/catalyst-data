"""Tests for ClusterCache — write-then-read, cache miss on key change, idempotent put.

Phase 3 (CD-80ic).
"""

from dagster_io.cluster_cache import ClusterCache, _InMemoryStore, _make_cluster_key


def _make_cluster(idx: int) -> dict:
    return {
        "cluster_id": f"cl-{idx}",
        "mention_indices": [idx],
        "doc_char_start": idx * 10,
        "doc_char_end": idx * 10 + 5,
    }


class TestClusterCache:
    def _cache(self) -> ClusterCache:
        """Return a ClusterCache backed by an in-memory store (no S3)."""
        return ClusterCache(store=_InMemoryStore(), code_location="test")

    # ── Basic write-then-read ───────────────────────────────────────────────

    def test_put_then_get_returns_clusters(self):
        cache = self._cache()
        clusters = [_make_cluster(0), _make_cluster(1)]
        params = {"threshold": 0.85, "proximity_radius": 512}

        cache.put("doc-1", "hello world", "gliner-large", params, clusters)
        result = cache.get("doc-1", "hello world", "gliner-large", params)

        assert result is not None
        assert len(result) == 2
        assert result[0]["cluster_id"] == "cl-0"
        assert result[1]["cluster_id"] == "cl-1"

    def test_miss_returns_none(self):
        cache = self._cache()
        result = cache.get("doc-99", "some text", "gliner-large", {})
        assert result is None

    # ── Content key change → cache miss ────────────────────────────────────

    def test_different_doc_text_is_a_miss(self):
        cache = self._cache()
        clusters = [_make_cluster(0)]
        cache.put("doc-1", "text A", "gliner-large", {}, clusters)

        # Different doc text → different cache key → miss
        result = cache.get("doc-1", "text B", "gliner-large", {})
        assert result is None

    def test_different_ner_model_is_a_miss(self):
        cache = self._cache()
        clusters = [_make_cluster(0)]
        cache.put("doc-1", "text A", "gliner-large", {}, clusters)

        result = cache.get("doc-1", "text A", "gliner-medium", {})
        assert result is None

    def test_different_params_is_a_miss(self):
        cache = self._cache()
        clusters = [_make_cluster(0)]
        cache.put("doc-1", "text A", "gliner-large", {"threshold": 0.85}, clusters)

        result = cache.get("doc-1", "text A", "gliner-large", {"threshold": 0.90})
        assert result is None

    # ── Idempotent put ──────────────────────────────────────────────────────

    def test_put_twice_same_key_is_idempotent(self):
        cache = self._cache()
        clusters = [_make_cluster(0)]
        params: dict = {}

        cache.put("doc-1", "text A", "gliner-large", params, clusters)
        cache.put("doc-1", "text A", "gliner-large", params, clusters)

        result = cache.get("doc-1", "text A", "gliner-large", params)
        assert result is not None
        assert len(result) == 1

    # ── get_or_compute ──────────────────────────────────────────────────────

    def test_get_or_compute_calls_fn_on_miss(self):
        cache = self._cache()
        calls = []

        def compute():
            calls.append(1)
            return [_make_cluster(0)]

        result = cache.get_or_compute("doc-1", "text", "gliner-large", {}, compute)
        assert len(result) == 1
        assert len(calls) == 1, "compute_fn should be called exactly once on a miss"

    def test_get_or_compute_skips_fn_on_hit(self):
        cache = self._cache()
        clusters = [_make_cluster(0)]
        cache.put("doc-1", "text", "gliner-large", {}, clusters)

        calls = []

        def compute():
            calls.append(1)
            return [_make_cluster(99)]

        result = cache.get_or_compute("doc-1", "text", "gliner-large", {}, compute)
        assert len(calls) == 0, "compute_fn should NOT be called on a cache hit"
        assert result[0]["cluster_id"] == "cl-0"  # original cached value

    def test_second_get_or_compute_is_fast(self):
        """Second invocation should return immediately without calling compute_fn."""
        import time

        cache = self._cache()

        def slow_compute():
            time.sleep(0.05)
            return [_make_cluster(0)]

        # First call — must compute
        cache.get_or_compute("doc-1", "text", "gliner-large", {}, slow_compute)

        # Second call — should hit cache, not sleep
        t0 = time.perf_counter()
        result = cache.get_or_compute("doc-1", "text", "gliner-large", {}, slow_compute)
        elapsed = time.perf_counter() - t0

        assert result is not None
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
        cache.put("doc-1", "text", "gliner-large", {}, [])
        result = cache.get("doc-1", "text", "gliner-large", {})
        # Should return an empty list, not None (miss)
        assert result is not None
        assert result == []
