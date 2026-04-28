"""Behavioral tests for ExtractionResource configuration and helpers.

Tests _is_encoder detection, _build_ner_config auto-tuning,
and ExtractionResult dataclass defaults. These tests use mocks and do not
require real LLM or MCP connections.
"""

from __future__ import annotations

import pytest
from catalyst_exgraph.protocol import ExtractionResult
from catalyst_exgraph.resource import ExtractionResource

# =====================================================================
# 1. _is_encoder detects encoder model names
# =====================================================================


class TestIsEncoder:
    """ExtractionResource._is_encoder correctly identifies encoder vs LLM models."""

    def _make(self, ner_model: str = "gpt-4o-mini") -> ExtractionResource:
        return ExtractionResource(ner_model=ner_model)

    @pytest.mark.parametrize(
        "model",
        ["gliner", "GLiNER-large", "GLINER_v2", "some-gliner-model"],
    )
    def test_gliner_models_detected_as_encoder(self, model):
        assert self._make(model)._is_encoder(model) is True

    @pytest.mark.parametrize(
        "model",
        ["nuextract", "NuExtract-v1.5", "NUEXTRACT"],
    )
    def test_nuextract_models_detected_as_encoder(self, model):
        assert self._make(model)._is_encoder(model) is True

    @pytest.mark.parametrize(
        "model",
        ["universalner", "UniversalNER-7B", "UNIVERSALNER"],
    )
    def test_universalner_models_detected_as_encoder(self, model):
        assert self._make(model)._is_encoder(model) is True

    @pytest.mark.parametrize(
        "model",
        ["uniner-7b", "UniNER-large"],
    )
    def test_uniner_alias_detected_as_encoder(self, model):
        assert self._make(model)._is_encoder(model) is True

    @pytest.mark.parametrize(
        "model",
        ["mistral:latest", "gpt-4o-mini", "llama3.1:8b", "claude-3-haiku", "qwen2:72b"],
    )
    def test_llm_models_not_detected_as_encoder(self, model):
        assert self._make(model)._is_encoder(model) is False


# =====================================================================
# 2. _build_ner_config with encoder model -> max_retries=0
# =====================================================================


class TestBuildNerConfig:
    """_build_ner_config auto-detects encoder models and sets max_retries=0."""

    def _make_resource(self, ner_model: str, **kwargs) -> ExtractionResource:
        """Create an ExtractionResource via normal constructor."""
        return ExtractionResource(
            ner_model=ner_model,
            spo_model=kwargs.get("spo_model", "gpt-4o-mini"),
            prompt_dir=kwargs.get("prompt_dir", ""),
            max_concurrency=5,
            ner_max_retries=kwargs.get("ner_max_retries", 3),
            spo_max_retries=3,
        )

    def test_encoder_model_gets_zero_retries(self):
        resource = self._make_resource("gliner")
        config = resource._build_ner_config()

        assert config.max_retries == 0
        assert config.stage_name == "ner"

    def test_llm_model_gets_configured_retries(self):
        resource = self._make_resource("mistral:latest", ner_max_retries=3)
        config = resource._build_ner_config()

        assert config.max_retries == 3
        assert config.stage_name == "ner"

    def test_custom_max_retries_for_llm(self):
        resource = self._make_resource("gpt-4o-mini", ner_max_retries=5)
        config = resource._build_ner_config()

        assert config.max_retries == 5

    def test_encoder_overrides_custom_max_retries(self):
        """Even if ner_max_retries=5, encoder models force max_retries=0."""
        resource = self._make_resource("nuextract", ner_max_retries=5)
        config = resource._build_ner_config()

        assert config.max_retries == 0


# =====================================================================
# 3. _build_spo_config always uses configured retries
# =====================================================================


class TestBuildSpoConfig:
    def _make_resource(self, spo_model: str = "mistral:latest", **kwargs) -> ExtractionResource:
        return ExtractionResource(
            ner_model="gpt-4o-mini",
            spo_model=spo_model,
            prompt_dir="",
            max_concurrency=5,
            ner_max_retries=3,
            spo_max_retries=kwargs.get("spo_max_retries", 3),
        )

    def test_spo_config_uses_configured_retries(self):
        resource = self._make_resource(spo_max_retries=5)
        config = resource._build_spo_config()

        assert config.max_retries == 5
        assert config.stage_name == "spo"

    def test_spo_config_default_retries(self):
        resource = self._make_resource()
        config = resource._build_spo_config()

        assert config.max_retries == 3


# =====================================================================
# 4. ExtractionResult dataclass
# =====================================================================


class TestExtractionResult:
    """ExtractionResult holds mentions, assertions, stats, and defaults to empty."""

    def test_defaults_to_empty_collections(self):
        result = ExtractionResult()

        assert result.mentions == []
        assert result.assertions == []
        assert result.stats == {}
        assert result.audit_events == []
        assert result.pipeline_breakdown == {}

    def test_holds_mentions_and_assertions(self):
        result = ExtractionResult(
            mentions=["m1", "m2"],
            assertions=["a1"],
            stats={"chunk_count": 3, "duration_s": 1.5},
        )

        assert len(result.mentions) == 2
        assert len(result.assertions) == 1
        assert result.stats["chunk_count"] == 3

    def test_audit_events_populated(self):
        events = [
            {"node_name": "extract_ner", "status": "completed", "duration_s": 0.1},
        ]
        result = ExtractionResult(audit_events=events)

        assert len(result.audit_events) == 1
        assert result.audit_events[0]["node_name"] == "extract_ner"

    def test_pipeline_breakdown_field(self):
        breakdown = {"ner": {"accepted": 5}, "spo": {"accepted": 3}}
        result = ExtractionResult(pipeline_breakdown=breakdown)

        assert result.pipeline_breakdown["ner"]["accepted"] == 5
