"""Test: _phase_a_build_cluster_cache saves per-encoder + ensemble fixtures (CD-z6xe).

Mocks ``_run_ensemble_for_doc`` so no real NER models are loaded.
Verifies:
- One per-encoder fixture saved for each encoder in the panel.
- One ``extraction_ensemble`` fixture saved.
- The 4-tuple return shape is correct.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dagster_io.cluster_cache import CachedNerResult
from tests.benchmark_config import ENCODER_MODELS

# ── Minimal BenchmarkStore stub ───────────────────────────────────────────────


class _FakeStore:
    """Minimal BenchmarkStore replacement — records save_fixture calls."""

    def __init__(self):
        self._fixtures: dict[str, dict] = {}

    def save_fixture(self, name: str, data) -> None:
        self._fixtures[name] = data

    def load_fixture(self, name: str):
        return self._fixtures.get(name)

    def list_fixtures(self) -> list[str]:
        return list(self._fixtures.keys())


# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _make_mention(text: str, encoder: str) -> dict:
    return {"text": text, "label": "PERSON", "confidence": 0.9, "_source_encoder": encoder}


def _make_consensus_mention(text: str) -> dict:
    return {"text": text, "canonical_type": "PERSON", "vote_count": 2, "n_encoders": 3}


def _make_cluster(idx: int) -> dict:
    return {"cluster_id": f"cl-{idx}", "mention_indices": [idx]}


def _make_window(idx: int) -> dict:
    return {"window_id": f"w-{idx}", "text": f"window text {idx}", "mention_indices": [idx]}


# 3 mock encoder configs from ENCODER_MODELS (encoder-tagged only)
_ENCODER_CFGS = [m for m in ENCODER_MODELS if "encoder" in m.tags][:3]

# Per-encoder mention lists keyed by encoder name
_ENC_MENTIONS: dict[str, list] = {}
for i, _cfg in enumerate(_ENCODER_CFGS):
    _ENC_MENTIONS[_cfg.name] = [_make_mention(f"Entity{i}-{_cfg.name}", _cfg.name)]

_CONSENSUS = [_make_consensus_mention("Alice"), _make_consensus_mention("Bob")]
_CLUSTERS = [_make_cluster(0)]
_WINDOWS = [_make_window(0)]
_REJECTED = [{"text": "Rejected0", "vote_count": 1}]


def _make_ner_result() -> CachedNerResult:
    """Fake CachedNerResult as if the ensemble pipeline ran over a doc."""
    return CachedNerResult(
        clusters=_CLUSTERS,
        mentions=_CONSENSUS,
        per_encoder_mentions=_ENC_MENTIONS,
        evidence_windows=_WINDOWS,
        rejected_mentions=_REJECTED,
    )


def _fake_chunk_dicts():
    """Two minimal valid TextChunk dicts for patching load_chunks."""
    return [
        {
            "chunk_id": "c1",
            "document_id": "doc-1",
            "text": "Alice and Bob met.",
            "index": 0,
            "total_chunks": 1,
            "metadata": {},
            "content_hash": "h1",
        },
        {
            "chunk_id": "c2",
            "document_id": "doc-2",
            "text": "Charlie runs Acme.",
            "index": 0,
            "total_chunks": 1,
            "metadata": {},
            "content_hash": "h2",
        },
    ]


# ── Shared test runner ────────────────────────────────────────────────────────


def _run_phase_a():
    """
    Invoke _phase_a_build_cluster_cache with all external I/O mocked.

    Patches:
    - ``load_chunks`` → 2 fake chunk dicts
    - ``_run_ensemble_for_doc`` → returns _make_ner_result() for each doc
    - ``_build_ensemble_pipeline_for_phase_a`` → returns a no-op MagicMock
    - ``tests.benchmark_config.ENCODER_MODELS`` → trimmed to 3 encoder cfgs
    """
    if len(_ENCODER_CFGS) < 3:
        pytest.skip("Need ≥3 encoder-tagged configs in ENCODER_MODELS for this test")

    store = _FakeStore()

    with (
        patch("tests.benchmark_harness.load_chunks", return_value=_fake_chunk_dicts()),
        patch("tests.benchmark_config.ENCODER_MODELS", _ENCODER_CFGS),
        patch(
            "tests.benchmark_harness._build_ensemble_pipeline_for_phase_a",
            return_value=MagicMock(),
        ),
        patch(
            "tests.benchmark_harness._run_ensemble_for_doc",
            side_effect=lambda _pipeline, _doc, _encoder_cfgs: _make_ner_result(),
        ),
    ):
        from tests.benchmark_harness import _phase_a_build_cluster_cache

        result = _phase_a_build_cluster_cache(
            sample_n=5,
            ner_ref_model=None,
            store=store,
        )

    return result, store, _ENCODER_CFGS


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestPhaseAFixtureEmission:
    """_phase_a_build_cluster_cache saves N per-encoder + 1 ensemble fixture."""

    def test_returns_four_tuple(self):
        """Phase A must return a 4-tuple (docs, clusters, mentions, per_encoder)."""
        result, store, enc_cfgs = _run_phase_a()
        assert len(result) == 4, "Expected (docs, clusters, mentions, per_encoder_by_doc)"
        docs, clusters, mentions, per_encoder = result
        assert isinstance(docs, list)
        assert isinstance(clusters, dict)
        assert isinstance(mentions, dict)
        assert isinstance(per_encoder, dict)

    def test_per_encoder_fixtures_saved_for_each_encoder(self):
        """One extraction_<model> fixture saved per encoder in the Phase A panel."""
        result, store, enc_cfgs = _run_phase_a()
        for enc_cfg in enc_cfgs:
            fixture_name = f"extraction_{enc_cfg.model}"
            assert fixture_name in store._fixtures, f"Expected fixture '{fixture_name}' to be saved by Phase A"

    def test_ensemble_fixture_saved(self):
        """extraction_ensemble fixture saved after Phase A."""
        result, store, enc_cfgs = _run_phase_a()
        assert "extraction_ensemble" in store._fixtures, "Expected 'extraction_ensemble' fixture to be saved by Phase A"

    def test_encoder_fixture_has_correct_shape(self):
        """Per-encoder fixture has expected keys and stats.phase = 'a_encoder'."""
        result, store, enc_cfgs = _run_phase_a()
        enc_cfg = enc_cfgs[0]
        fixture = store._fixtures.get(f"extraction_{enc_cfg.model}")
        assert fixture is not None
        assert "mentions" in fixture
        assert "assertions" in fixture
        assert isinstance(fixture["assertions"], list) and fixture["assertions"] == []
        assert fixture["stats"]["phase"] == "a_encoder"

    def test_ensemble_fixture_has_correct_shape(self):
        """Ensemble fixture has expected keys, model='ensemble', and stats.n_encoders."""
        result, store, enc_cfgs = _run_phase_a()
        fixture = store._fixtures["extraction_ensemble"]
        assert fixture["model"] == "ensemble"
        assert "mentions" in fixture
        assert isinstance(fixture["mentions"], list)
        assert fixture["stats"]["phase"] == "a_ensemble"
        assert fixture["stats"]["n_encoders"] == len(enc_cfgs)

    def test_per_encoder_by_doc_maps_encoder_to_doc_to_mentions(self):
        """The 4th tuple element maps encoder_name → {doc_id → [mentions]}."""
        result, store, enc_cfgs = _run_phase_a()
        docs, clusters, mentions, per_encoder = result
        for enc_cfg in enc_cfgs:
            assert enc_cfg.name in per_encoder, f"Encoder '{enc_cfg.name}' missing from per_encoder_by_doc"

    def test_consensus_mentions_flow_into_mentions_by_doc(self):
        """mentions_by_doc contains the consensus mentions, not raw per-encoder lists."""
        result, store, enc_cfgs = _run_phase_a()
        docs, clusters, mentions, per_encoder = result
        for doc_id, doc_mentions in mentions.items():
            assert isinstance(doc_mentions, list)
            # Consensus mentions have canonical_type and vote_count
            for m in doc_mentions:
                assert "vote_count" in m or "canonical_type" in m, (
                    f"Expected consensus mention fields in mentions_by_doc[{doc_id!r}], got: {m}"
                )

    def test_exactly_one_ensemble_fixture_and_n_encoder_fixtures(self):
        """Total fixture count = 1 ensemble + len(enc_cfgs) encoder fixtures."""
        result, store, enc_cfgs = _run_phase_a()
        encoder_fixture_keys = [
            k for k in store._fixtures if k.startswith("extraction_") and k != "extraction_ensemble"
        ]
        assert len(encoder_fixture_keys) == len(enc_cfgs)
        assert "extraction_ensemble" in store._fixtures
