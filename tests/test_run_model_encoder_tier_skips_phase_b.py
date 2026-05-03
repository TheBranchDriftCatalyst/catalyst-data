"""Test: _run_model with encoder-tier tag returns pre-saved fixture, skips SPO (CD-z6xe).

Verifies:
- A model with "encoder" in cfg.tags that is in phase_a_encoder_names → reads
  the pre-saved fixture from the store and returns it, without invoking
  extract_with_shared_clusters or extract_validated.
- A model with "encoder" tag that is NOT in phase_a_encoder_names → falls
  through to the legacy extraction path.
- The "ensemble" synthetic model → reads extraction_ensemble fixture.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.benchmark_config import ModelConfig


def _make_encoder_cfg(name: str = "gliner-large", model: str = "gliner-large") -> ModelConfig:
    return ModelConfig(
        name=name,
        model=model,
        base_url="",
        tags=["encoder", "extraction-specialist", "600m"],
    )


def _make_ensemble_cfg() -> ModelConfig:
    return ModelConfig(
        name="ensemble",
        model="ensemble",
        base_url="",
        tags=["encoder", "ensemble", "v4"],
    )


def _make_llm_cfg(name: str = "mistral-7b") -> ModelConfig:
    return ModelConfig(
        name=name,
        model="mistral:latest",
        base_url="http://localhost:11434/v1",
        tags=["ollama", "7b", "tier2"],
    )


def _pre_saved_fixture(model: str) -> dict:
    return {
        "model": model,
        "base_url": "",
        "structured_method": "gliner",
        "mentions": [{"text": "Alice", "label": "PERSON"}],
        "assertions": [],
        "stats": {"mention_count": 1, "assertion_count": 0, "phase": "a_encoder"},
    }


class TestEncoderTierShortcut:
    """Encoder-tier models in Phase A panel skip SPO and return the cached fixture."""

    def _run(self, cfg: ModelConfig, phase_a_encoder_names: set, store_fixtures: dict):
        from tests.benchmark_harness import _run_model

        store = MagicMock()
        store.load_fixture.side_effect = lambda name: store_fixtures.get(name)
        store.load_run.return_value = None

        with (
            patch("dagster_io.extraction.extract_with_shared_clusters") as mock_spo,
            patch("dagster_io.extraction.extract_validated") as mock_full,
        ):
            fixture = _run_model(
                cfg=cfg,
                timeout=30,
                store=store,
                run_id="test-run",
                shared_docs=[MagicMock(doc_id="doc-1", chunks=[], full_text="x")],
                shared_clusters={"doc-1": []},
                shared_mentions={"doc-1": []},
                phase_a_encoder_names=phase_a_encoder_names,
            )
            return fixture, mock_spo, mock_full

    def test_encoder_in_panel_returns_pre_saved_fixture(self):
        """Encoder-tier model in Phase A panel reads the fixture, returns it."""
        cfg = _make_encoder_cfg("gliner-large", "gliner-large")
        pre_saved = _pre_saved_fixture("gliner-large")
        fixture, mock_spo, mock_full = self._run(
            cfg,
            phase_a_encoder_names={"gliner-large"},
            store_fixtures={"extraction_gliner-large": pre_saved},
        )
        assert fixture is not None
        assert fixture["model"] == "gliner-large"
        assert fixture["stats"]["phase"] == "a_encoder"
        mock_spo.assert_not_called()
        mock_full.assert_not_called()

    def test_encoder_in_panel_does_not_call_spo(self):
        """extract_with_shared_clusters is never called for encoder-tier models."""
        cfg = _make_encoder_cfg("gliner-medium", "gliner")
        pre_saved = _pre_saved_fixture("gliner")
        _, mock_spo, _ = self._run(
            cfg,
            phase_a_encoder_names={"gliner-medium"},
            store_fixtures={"extraction_gliner": pre_saved},
        )
        mock_spo.assert_not_called()

    def test_ensemble_synthetic_model_reads_ensemble_fixture(self):
        """'ensemble' model name reads extraction_ensemble, skips SPO."""
        cfg = _make_ensemble_cfg()
        ensemble_fixture = {
            "model": "ensemble",
            "mentions": [{"text": "Alice", "canonical_type": "PERSON", "vote_count": 2}],
            "assertions": [],
            "stats": {"phase": "a_ensemble", "n_encoders": 3},
        }
        fixture, mock_spo, _ = self._run(
            cfg,
            phase_a_encoder_names={"gliner-large"},
            store_fixtures={"extraction_ensemble": ensemble_fixture},
        )
        assert fixture is not None
        assert fixture["model"] == "ensemble"
        assert fixture["stats"]["phase"] == "a_ensemble"
        mock_spo.assert_not_called()

    def test_llm_tier_model_calls_spo_not_shortcut(self):
        """LLM-tier model (no 'encoder' tag) always goes to extract_with_shared_clusters."""
        cfg = _make_llm_cfg("mistral-7b")

        mock_mention = MagicMock()
        mock_mention.model_dump.return_value = {"text": "Alice", "mention_type": "PERSON"}
        mock_assertion = MagicMock()
        mock_assertion.model_dump.return_value = {}

        store = MagicMock()
        store.load_run.return_value = None

        from tests.benchmark_harness import _run_model

        with (
            patch(
                "dagster_io.extraction.extract_with_shared_clusters", return_value=([mock_mention], [mock_assertion])
            ) as mock_spo,
            patch(
                "os.environ",
                dict(
                    BENCH_SAMPLE_PER_DOMAIN="5",
                    LLM_MODEL="mistral:latest",
                    LLM_BASE_URL="http://localhost:11434/v1",
                    PROMPT_REGISTRY_DIR="/tmp",
                    CATALYST_BENCH_MODEL="mistral-7b",
                ),
            ),
        ):
            _run_model(
                cfg=cfg,
                timeout=30,
                store=store,
                run_id="test-run",
                shared_docs=[MagicMock(doc_id="doc-1", chunks=[], full_text="x")],
                shared_clusters={"doc-1": []},
                shared_mentions={"doc-1": []},
                phase_a_encoder_names={"gliner-large"},
            )

        mock_spo.assert_called_once()

    def test_encoder_not_in_panel_falls_through(self):
        """Encoder tag but NOT in phase_a_encoder_names → legacy path (extract_validated)."""
        cfg = _make_encoder_cfg("nuextract-1.5", "nuextract1.5:latest")
        # Not in the ensemble panel
        phase_a_encoder_names = {"gliner-large", "gliner-pii"}

        store = MagicMock()
        store.load_run.return_value = None

        mock_mention = MagicMock()
        mock_mention.model_dump.return_value = {"text": "Alice", "mention_type": "PERSON"}

        # TextChunk requires total_chunks; include it here.
        _fake_chunks = [
            {
                "text": "test",
                "document_id": "doc-1",
                "chunk_id": "c1",
                "index": 0,
                "total_chunks": 1,
                "metadata": {},
                "content_hash": "h1",
            }
        ]

        from tests.benchmark_harness import _run_model

        with (
            patch("dagster_io.extraction.extract_validated", return_value=([mock_mention], [])) as mock_full,
            patch("tests.shared.medallion.load_chunks", return_value=_fake_chunks),
            # Patch load_chunks at both import sites to be safe
            patch("tests.benchmark_harness.load_chunks", return_value=_fake_chunks),
            patch(
                "os.environ",
                dict(
                    BENCH_SAMPLE_PER_DOMAIN="5",
                    LLM_MODEL="nuextract1.5:latest",
                    LLM_BASE_URL="",
                    PROMPT_REGISTRY_DIR="/tmp",
                    CATALYST_BENCH_MODEL="nuextract-1.5",
                ),
            ),
        ):
            _run_model(
                cfg=cfg,
                timeout=30,
                store=store,
                run_id="test-run",
                shared_docs=None,  # no shared_clusters triggers legacy path
                shared_clusters=None,
                shared_mentions=None,
                phase_a_encoder_names=phase_a_encoder_names,
            )

        mock_full.assert_called_once()
