"""Behavioral tests for StageConfig and PipelineConfig."""

from __future__ import annotations

import dataclasses

import pytest
from catalyst_exgraph.config import (
    PipelineConfig,
    StageConfig,
    ner_stage_config,
)

from .conftest import DummyOutput

# ── Skipped stage behavior ──────────────────────────────────────────────────


def test_skipped_stage_reports_skip_true(skipped_stage_config: StageConfig):
    assert skipped_stage_config.skip is True


def test_skipped_stage_in_pipeline_stages_but_not_active(
    dummy_stage_config: StageConfig,
    skipped_stage_config: StageConfig,
):
    pipeline = PipelineConfig(stages=[dummy_stage_config, skipped_stage_config])

    assert skipped_stage_config in pipeline.stages
    assert skipped_stage_config not in pipeline.active_stages
    assert dummy_stage_config in pipeline.active_stages


# ── Preset config schemas ───────────────────────────────────────────────────


def test_ner_stage_config_uses_mention_extraction_schema(ner_config: StageConfig):
    from catalyst_contracts.models.extraction_output import MentionExtractionResult

    assert ner_config.extraction_schema is MentionExtractionResult


def test_spo_stage_config_uses_proposition_extraction_schema(spo_config: StageConfig):
    from catalyst_contracts.models.extraction_output import PropositionExtractionResult

    assert spo_config.extraction_schema is PropositionExtractionResult


# ── default_pipeline_config ─────────────────────────────────────────────────


def test_default_pipeline_has_exactly_two_stages(pipeline_config: PipelineConfig):
    assert len(pipeline_config.stages) == 2


def test_default_pipeline_stage_order_is_ner_then_spo(pipeline_config: PipelineConfig):
    names = pipeline_config.stage_names
    assert names == ["ner", "spo"]


# ── get_stage lookup ────────────────────────────────────────────────────────


def test_get_stage_returns_matching_config(pipeline_config: PipelineConfig):
    ner = pipeline_config.get_stage("ner")
    assert ner is not None
    assert ner.stage_name == "ner"


def test_get_stage_returns_none_for_nonexistent(pipeline_config: PipelineConfig):
    assert pipeline_config.get_stage("nonexistent") is None


# ── StageConfig with max_retries=0 (encoder mode) ──────────────────────────


def test_stage_config_max_retries_zero_is_valid():
    cfg = StageConfig(
        stage_name="encoder",
        extraction_schema=DummyOutput,
        prompt_id="enc",
        validation_tool="validate_enc",
        repair_prompt_id="repair_enc",
        max_retries=0,
    )
    assert cfg.max_retries == 0


# ── model_override propagation ──────────────────────────────────────────────


def test_stage_config_model_override_propagates():
    cfg = ner_stage_config(model="custom-model-7b")
    assert cfg.model_override == "custom-model-7b"


def test_stage_config_model_override_defaults_to_none(ner_config: StageConfig):
    assert ner_config.model_override is None


# ── Empty stages list ───────────────────────────────────────────────────────


def test_pipeline_with_empty_stages_has_no_active_stages():
    pipeline = PipelineConfig(stages=[])
    assert pipeline.active_stages == []
    assert pipeline.stage_names == []


# ── Frozen (immutable) dataclass ────────────────────────────────────────────


def test_stage_config_is_frozen(ner_config: StageConfig):
    with pytest.raises(dataclasses.FrozenInstanceError):
        ner_config.stage_name = "mutated"  # type: ignore[misc]


def test_pipeline_config_is_frozen(pipeline_config: PipelineConfig):
    with pytest.raises(dataclasses.FrozenInstanceError):
        pipeline_config.max_concurrency = 99  # type: ignore[misc]
