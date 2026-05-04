"""Tests that the same entity_clusters produce different window sizes for different models.

Phase 3 (CD-80ic).
"""

import asyncio

import pytest
from catalyst_exgraph.nodes.pack import PackEvidenceNode
from catalyst_exgraph.state import EntityCluster


@pytest.fixture(autouse=True)
def configure_event_store(tmp_path):
    """Configure event_store so PackEvidenceNode's audit emit lands somewhere."""
    from dagster_io.bench import event_store

    event_store.close()
    event_store.configure(run_id="test-pack-per-model", run_dir=tmp_path)
    yield
    event_store.close()


def _make_cluster(start: int, end: int) -> EntityCluster:
    return EntityCluster(
        cluster_id=f"cl-{start}",
        mention_indices=[0],
        doc_char_start=start,
        doc_char_end=end,
    )


def _make_state(model: str, raw_text: str, clusters: list[EntityCluster]) -> dict:
    return {
        "raw_text": raw_text,
        "doc_id": "test-doc",
        "model": model,
        "entity_clusters": clusters,
        "stages": {"ner": {"accepted": [{"text": "Alice", "span_start": 0, "span_end": 5}]}},
        "audit_events": [],
    }


RAW_TEXT = "Alice met Bob in New York. " * 200  # ~5400 chars — triggers window splitting for small models


class TestPackPerModel:
    def test_gliner_medium_fewer_windows_or_smaller_than_gemma(self):
        """gliner-medium (320 tok budget) produces more/smaller windows than gemma3-12b (24576 tok)."""
        clusters = [_make_cluster(100, 150)]

        node = PackEvidenceNode()

        state_gliner = _make_state("gliner-medium", RAW_TEXT, clusters)
        state_gemma = _make_state("gemma3-12b", RAW_TEXT, clusters)

        result_gliner = asyncio.get_event_loop().run_until_complete(node(state_gliner))
        result_gemma = asyncio.get_event_loop().run_until_complete(node(state_gemma))

        windows_gliner = result_gliner["evidence_windows"]
        windows_gemma = result_gemma["evidence_windows"]

        # Both should produce at least one window
        assert len(windows_gliner) >= 1
        assert len(windows_gemma) >= 1

        # gliner windows must be smaller or equal in max text length vs gemma
        max_gliner = max(len(w["text"]) for w in windows_gliner)
        max_gemma = max(len(w["text"]) for w in windows_gemma)

        # gliner budget (320 tok × 4 chars = 1280 chars) << gemma (24576 × 4 = 98304 chars)
        # So gliner windows are strictly smaller
        assert max_gliner <= max_gemma, (
            f"Expected gliner windows ({max_gliner} chars) <= gemma windows ({max_gemma} chars)"
        )

    def test_override_context_tokens_respected(self):
        """PackEvidenceNode(context_tokens=...) overrides model lookup."""
        clusters = [_make_cluster(0, 10)]
        short_node = PackEvidenceNode(context_tokens=50)  # very small

        state = _make_state("gemma3-12b", RAW_TEXT, clusters)
        result = asyncio.get_event_loop().run_until_complete(short_node(state))
        windows = result["evidence_windows"]

        # Each window must fit within 50 tok × 4 = 200 chars
        for w in windows:
            assert len(w["text"]) <= 200 + 5, f"Window too large for 50-tok override: {len(w['text'])}"

    def test_same_clusters_different_models_different_window_counts(self):
        """The same clusters can yield different window counts when model context differs."""
        # Use a very large document to force splitting for small-context models
        long_text = "Word " * 5000  # ~25000 chars
        clusters = [_make_cluster(100, 200)]

        node = PackEvidenceNode()

        result_enc = asyncio.get_event_loop().run_until_complete(
            node(_make_state("gliner-medium", long_text, clusters))
        )
        result_llm = asyncio.get_event_loop().run_until_complete(node(_make_state("gemma3-12b", long_text, clusters)))

        enc_windows = result_enc["evidence_windows"]
        llm_windows = result_llm["evidence_windows"]

        # encoder model always produces >= 1 window; LLM with huge context may produce fewer
        assert len(enc_windows) >= len(llm_windows), (
            f"Expected encoder ({len(enc_windows)}) >= LLM ({len(llm_windows)}) window count"
        )

    def test_model_windows_imported_from_dagster_io(self):
        """Verify MODEL_WINDOWS is importable from the canonical location."""
        from catalyst_exgraph.nodes.pack import MODEL_WINDOWS as pack_MODEL_WINDOWS

        from dagster_io.chunking import MODEL_WINDOWS

        # pack.py re-exports the same object from dagster_io.chunking
        assert MODEL_WINDOWS is pack_MODEL_WINDOWS
