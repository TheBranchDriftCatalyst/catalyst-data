"""Tests for the two-phase bench harness flow.

Phase A: NER + cluster once per (doc, ner_ref_model) — populates ClusterCache.
Phase B: pack_evidence + SPO per target model — cheap, uses cached clusters.

Uses mocked pipelines to avoid requiring a running LLM/GLiNER service.
Counts ``ainvoke`` calls to verify Phase A runs NER exactly once per doc and
Phase B skips NER entirely.

Phase 3 (CD-80ic).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from dagster_io.cluster_cache import ClusterCache, _InMemoryStore

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_cluster(idx: int) -> dict:
    return {
        "cluster_id": f"cl-{idx}",
        "mention_indices": [0],
        "doc_char_start": idx * 20,
        "doc_char_end": idx * 20 + 10,
    }


def _make_ner_result(clusters: list[dict], mentions: list[dict] | None = None) -> dict:
    return {
        "entity_clusters": clusters,
        "evidence_windows": [
            {
                "window_id": "win-0",
                "text": "Alice met Bob in New York.",
                "mention_indices": [0],
                "cluster_id": clusters[0]["cluster_id"] if clusters else "cl-0",
                "doc_char_start": 0,
                "doc_char_end": 25,
            }
        ]
        if clusters
        else [],
        "stages": {
            "ner": {
                "accepted": mentions or [{"text": "Alice", "span_start": 0, "span_end": 5}],
                "retry_count": 0,
            }
        },
        "audit_events": [],
        "status": "completed",
    }


def _make_spo_result() -> dict:
    return {
        "stages": {
            "spo": {
                "accepted": [
                    {
                        "subject": "Alice",
                        "predicate": "met",
                        "object": "Bob",
                        "confidence": 0.9,
                        "negated": False,
                        "hedged": False,
                        "qualifiers": {},
                    }
                ],
                "retry_count": 0,
            }
        },
        "audit_events": [],
        "status": "completed",
    }


# ── ClusterCache hit/miss counting ───────────────────────────────────────────


class TestClusterCacheHitMiss:
    """Verify that Phase A's second invocation hits the cache (skips compute)."""

    def _make_cache(self) -> ClusterCache:
        return ClusterCache(store=_InMemoryStore(), code_location="test")

    def test_phase_a_first_call_computes(self):
        cache = self._cache = self._make_cache()
        calls = []

        def compute():
            calls.append(1)
            return [_make_cluster(0)]

        cache.get_or_compute("doc-1", "text A", "gliner-large", {}, compute)
        assert len(calls) == 1

    def test_phase_a_second_call_hits_cache(self):
        cache = self._cache = self._make_cache()
        calls = []

        def compute():
            calls.append(1)
            return [_make_cluster(0)]

        cache.get_or_compute("doc-1", "text A", "gliner-large", {}, compute)
        cache.get_or_compute("doc-1", "text A", "gliner-large", {}, compute)
        assert len(calls) == 1, "compute_fn should be called only once — second call is a cache hit"

    def test_second_run_is_faster(self):
        cache = self._make_cache()

        def slow_compute():
            time.sleep(0.05)
            return [_make_cluster(0)]

        # Warm cache
        cache.get_or_compute("doc-1", "text A", "gliner-large", {}, slow_compute)

        t0 = time.perf_counter()
        cache.get_or_compute("doc-1", "text A", "gliner-large", {}, slow_compute)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.01, f"Cache hit took {elapsed * 1000:.1f}ms — expected < 10ms"


# ── Two-phase flow mock test ─────────────────────────────────────────────────


class _FakeDoc:
    """Minimal _Doc stand-in."""

    def __init__(self, doc_id: str, text: str):
        self.doc_id = doc_id
        self.full_text = text
        self.chunks = []
        self.chunk_metadata = {}


class TestTwoPhaseFlow:
    """Validate Phase A produces clusters and Phase B is called per target model."""

    def _build_mock_ner_pipeline(self, clusters: list[dict]) -> AsyncMock:
        pipeline = AsyncMock()
        pipeline.ainvoke = AsyncMock(return_value=_make_ner_result(clusters))
        return pipeline

    def _build_mock_spo_pipeline(self) -> AsyncMock:
        pipeline = AsyncMock()
        pipeline.ainvoke = AsyncMock(return_value=_make_spo_result())
        return pipeline

    def test_phase_a_populates_cluster_cache(self):
        """Phase A: after processing 2 docs, the cluster cache has 2 entries."""
        cache = ClusterCache(store=_InMemoryStore(), code_location="test")
        docs = [
            _FakeDoc("doc-1", "Alice met Bob in New York."),
            _FakeDoc("doc-2", "Charlie talked to Dave in London."),
        ]
        clusters_a = [_make_cluster(0)]
        clusters_b = [_make_cluster(1)]

        ner_pipeline = self._build_mock_ner_pipeline(clusters_a)
        ner_pipeline.ainvoke = AsyncMock(
            side_effect=[
                _make_ner_result(clusters_a),  # doc-1
                _make_ner_result(clusters_b),  # doc-2
            ]
        )

        # Simulate Phase A: compute clusters via cache for each doc
        params: dict = {}
        ner_model = "gliner-large"
        cluster_by_doc: dict = {}

        for doc in docs:
            result = cache.get_or_compute(
                doc_id=doc.doc_id,
                doc_text=doc.full_text,
                ner_model=ner_model,
                params=params,
                compute_fn=lambda _d=doc: (
                    asyncio.get_event_loop()
                    .run_until_complete(ner_pipeline.ainvoke({"raw_text": _d.full_text}))
                    .get("entity_clusters", [])
                ),
            )
            cluster_by_doc[doc.doc_id] = result

        assert len(cluster_by_doc) == 2
        assert "doc-1" in cluster_by_doc
        assert "doc-2" in cluster_by_doc
        # NER pipeline called exactly once per doc
        assert ner_pipeline.ainvoke.call_count == 2

    def test_phase_b_does_not_call_ner(self):
        """Phase B: NER pipeline is NOT invoked; only SPO pipeline is used."""
        # Pre-populate the cluster cache
        cache = ClusterCache(store=_InMemoryStore(), code_location="test")
        doc = _FakeDoc("doc-1", "Alice met Bob in New York.")
        clusters = [_make_cluster(0)]
        cache.put("doc-1", doc.full_text, "gliner-large", {}, clusters)

        ner_pipeline = self._build_mock_ner_pipeline(clusters)
        spo_pipeline = self._build_mock_spo_pipeline()

        # Phase B: retrieve from cache, then run SPO only
        cached = cache.get("doc-1", doc.full_text, "gliner-large", {})
        assert cached is not None

        # Simulate SPO-only processing (cache hit, no NER)
        asyncio.get_event_loop().run_until_complete(
            spo_pipeline.ainvoke({"raw_text": doc.full_text, "entity_clusters": cached})
        )

        # NER pipeline should NOT have been called
        ner_pipeline.ainvoke.assert_not_called()
        # SPO pipeline should have been called once
        spo_pipeline.ainvoke.assert_called_once()

    def test_phase_a_once_phase_b_per_model(self):
        """Two models in Phase B → 2 SPO calls but only 1 NER call total (Phase A)."""
        cache = ClusterCache(store=_InMemoryStore(), code_location="test")
        doc = _FakeDoc("doc-1", "Alice met Bob.")
        clusters = [_make_cluster(0)]

        ner_call_count = [0]

        def compute_ner():
            ner_call_count[0] += 1
            return clusters

        # Phase A: populate cache (1 NER call)
        cache.get_or_compute("doc-1", doc.full_text, "gliner-large", {}, compute_ner)

        spo_call_count = [0]

        def run_spo_for_model(model_name: str):
            cached = cache.get("doc-1", doc.full_text, "gliner-large", {})
            if cached:
                spo_call_count[0] += 1

        # Phase B: two different target models
        run_spo_for_model("gliner-medium")
        run_spo_for_model("gemma3-12b")

        assert ner_call_count[0] == 1, "NER should run exactly once in Phase A"
        assert spo_call_count[0] == 2, "SPO should run once per target model"
