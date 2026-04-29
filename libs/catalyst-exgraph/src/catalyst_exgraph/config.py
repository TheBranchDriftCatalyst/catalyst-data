"""Extraction pipeline configuration.

StageConfig parameterizes a single extract→validate→repair loop.
PipelineConfig composes multiple stages into a pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass(frozen=True)
class StageConfig:
    """Configuration for one extraction stage (NER or SPO).

    Each stage runs: extract → validate → repair(loop) for one extraction type.
    The stage is parameterized by the Pydantic output schema, prompt IDs, and
    the MCP validation tool name.
    """

    stage_name: str
    """Unique name for this stage (e.g. 'ner', 'spo'). Used as key in ExGraphState.stages."""

    extraction_schema: type[BaseModel]
    """Pydantic model for structured LLM output (e.g. MentionExtractionResult)."""

    prompt_id: str
    """Prompt registry ID for extraction (e.g. 'mention_extraction')."""

    validation_tool: str
    """MCP validator tool name (e.g. 'validate_mentions')."""

    repair_prompt_id: str
    """Prompt registry ID for repair (e.g. 'mention_repair')."""

    fallback_prompt: str = ""
    """Fallback prompt text when prompt registry file is missing."""

    fallback_repair_prompt: str = ""
    """Fallback repair prompt text."""

    prompt_dir: str | None = None
    """Directory containing .prompt files. Overrides PROMPT_REGISTRY_DIR env var.
    Set per-stage or per-resource to use domain-specific prompts
    (e.g. k8s/media-ingest/prompts vs k8s/congress-data/prompts)."""

    max_retries: int = 3
    """Max repair cycles. Set to 0 for encoder models (deterministic output)."""

    model_override: str | None = None
    """Override LLM_MODEL env var for this stage (enables per-stage model selection)."""

    skip: bool = False
    """Skip this stage entirely (pass-through)."""

    # Phase 4: Ensemble
    ensemble_models: list[str] | None = None
    """List of model names for ensemble extraction. None = single model."""

    consensus_strategy: str = "majority"
    """Ensemble voting strategy: 'majority', 'unanimous', or 'any'."""

    consensus_threshold: float = 0.5
    """Minimum fraction of models that must agree for consensus."""


@dataclass(frozen=True)
class PipelineConfig:
    """Configuration for a multi-stage extraction pipeline.

    Stages are executed in order. Each stage's accepted output is available
    to subsequent stages via upstream_context.
    """

    stages: list[StageConfig] = field(default_factory=list)
    """Ordered list of extraction stages to execute."""

    max_concurrency: int = 5
    """Max parallel chunk processing."""

    def get_stage(self, name: str) -> StageConfig | None:
        """Look up a stage by name."""
        for s in self.stages:
            if s.stage_name == name:
                return s
        return None

    @property
    def stage_names(self) -> list[str]:
        return [s.stage_name for s in self.stages]

    @property
    def active_stages(self) -> list[StageConfig]:
        """Stages that are not skipped."""
        return [s for s in self.stages if not s.skip]


# ── Preset configs ────────────────────────────────────────────────────


def ner_stage_config(
    model: str | None = None,
    max_retries: int = 3,
    ensemble_models: list[str] | None = None,
) -> StageConfig:
    """Create a standard NER (mention extraction) stage config."""
    from catalyst_contracts.models.extraction_output import MentionExtractionResult

    return StageConfig(
        stage_name="ner",
        extraction_schema=MentionExtractionResult,
        prompt_id="mention_extraction",
        validation_tool="validate_mentions",
        repair_prompt_id="mention_repair",
        max_retries=max_retries,
        model_override=model,
        ensemble_models=ensemble_models,
    )


def spo_stage_config(
    model: str | None = None,
    max_retries: int = 3,
    skip: bool = False,
) -> StageConfig:
    """Create a standard SPO (proposition extraction) stage config."""
    from catalyst_contracts.models.extraction_output import PropositionExtractionResult

    return StageConfig(
        stage_name="spo",
        extraction_schema=PropositionExtractionResult,
        prompt_id="proposition_extraction",
        validation_tool="validate_propositions",
        repair_prompt_id="proposition_repair",
        max_retries=max_retries,
        model_override=model,
        skip=skip,
    )


def default_pipeline_config(
    ner_model: str | None = None,
    spo_model: str | None = None,
) -> PipelineConfig:
    """Create the standard NER→SPO pipeline config."""
    return PipelineConfig(
        stages=[
            ner_stage_config(model=ner_model),
            spo_stage_config(model=spo_model),
        ],
    )
