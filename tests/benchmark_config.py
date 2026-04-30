"""Central configuration for extraction benchmarks.

Defines which models/endpoints to test against. Edit this file to add or
remove models from the benchmark suite.

Target: ≤12B params for local inference on Apple Silicon.
Selection based on LLMStructBench (arxiv:2602.14743) composite scores,
GLiNER NER benchmarks, and our own empirical results.

Usage:
    # Run all configured models:
    pytest tests/test_extraction_benchmark.py -k "run_all" -v -s

    # Override with a single model via env var:
    LLM_MODEL=mistral:latest LLM_BASE_URL=http://localhost:11434/v1 \
        pytest tests/test_extraction_benchmark.py -k "extraction" -v -s
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dagster_io.chunking import ChunkConfig


@dataclass
class ModelConfig:
    """Configuration for a single model to benchmark."""

    name: str  # short display name for reports
    model: str  # model ID passed to the API
    base_url: str  # OpenAI-compatible API base URL
    structured_method: str = "json_mode"  # json_mode works best for local models
    api_key: str = "unused"
    max_tokens: int = 4096
    timeout: int = 300  # 5 minutes per model
    context_window: int = 4096  # max input tokens the model accepts
    tags: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Ollama models — json_mode for structured output
# ═══════════════════════════════════════════════════════════════════════════

OLLAMA_BASE = "http://localhost:11434/v1"

# ── Extraction specialists ────────────────────────────────────────────────
# Purpose-built for NER, not general LLMs

EXTRACTION_MODELS = [
    ModelConfig(
        name="nuextract-1.5",
        model="nuextract1.5:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "extraction-specialist", "3.8b"],
    ),
    # nuextract 3.8b (v1.0) — removed: degenerates on long text, slow sliding window
    # nuextract 2.0-8B — removed: Qwen2.5-VL GGUF template issues, 0 mentions
    ModelConfig(
        name="universalner-7b",
        model="universalner:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "extraction-specialist", "7b"],
    ),
]

# ── GLiNER (encoder, not an LLM) ─────────────────────────────────────────
# Runs locally via Python, no serving endpoint. Set LLM_MODEL=gliner to activate.
# 87% F1 zero-shot NER, 0.1s inference, 300M params.

ENCODER_MODELS = [
    ModelConfig(
        name="gliner-medium",
        model="gliner",  # triggers GLiNERClient in _build_graph()
        base_url="",  # not used — runs in-process
        context_window=512,
        tags=["encoder", "extraction-specialist", "300m"],
    ),
    ModelConfig(
        name="gliner-large",
        model="gliner-large",  # resolved via GLINER_MODEL env var in subprocess
        base_url="",
        context_window=512,
        tags=["encoder", "extraction-specialist", "600m"],
    ),
    ModelConfig(
        name="gliner-pii",
        model="gliner-pii",  # PII + general NER — detects phone, email, SSN + standard entities
        base_url="",
        context_window=512,
        tags=["encoder", "extraction-specialist", "300m"],
    ),
    ModelConfig(
        name="nuextract-2.0-8b",
        model="nuextract2:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "extraction-specialist", "8b"],
    ),
]

# ── Tier 1: Best composite scores on LLMStructBench ──────────────────────
# These scored highest on structured extraction benchmarks.
# Source: arxiv:2602.14743 Table V

TIER1_MODELS = [
    # LLMStructBench 0.72 — beats 70B models, best ≤12B
    ModelConfig(
        name="gemma3-12b",
        model="gemma3:12b",
        base_url=OLLAMA_BASE,
        context_window=8192,
        tags=["ollama", "12b", "tier1"],
    ),
    # DeepSeek-R1 7B — LLMStructBench 0.67 but reasoning models are too slow
    # for benchmark (10+ min per chunk due to <think> phase). Use for
    # quality comparison only, not routine benchmarks.
    # ModelConfig(
    #     name="deepseek-r1-7b",
    #     model="deepseek-r1:7b",
    #     base_url=OLLAMA_BASE,
    #     tags=["ollama", "7b", "tier1", "reasoning"],
    # ),
    # phi4-mini — removed: timeouts on json_mode extraction
    # qwen3-4b — removed: timeouts on json_mode extraction
    # qwen3-8b — removed: timeouts on json_mode extraction
    # These models may work with function_calling or need /no_think prompting
]

# ── Tier 2: Strong empirical performers (our benchmark) ───────────────────
# Not in LLMStructBench but performed well in our extraction pipeline tests.

TIER2_MODELS = [
    # Our benchmark: 23 mentions (best recall), 72s, 3 retries
    ModelConfig(
        name="mistral-7b",
        model="mistral:latest",
        base_url=OLLAMA_BASE,
        context_window=8192,
        tags=["ollama", "7b", "tier2"],
    ),
    # Our benchmark: 22 mentions, 12 assertions (best balanced), 95s
    ModelConfig(
        name="qwen2.5-7b",
        model="qwen2.5:7b-instruct",
        base_url=OLLAMA_BASE,
        context_window=8192,
        tags=["ollama", "7b", "tier2"],
    ),
    # Our benchmark: 17 mentions, 29 assertions (best SPO), 88s
    ModelConfig(
        name="llama3.1-8b",
        model="llama3.1:8b",
        base_url=OLLAMA_BASE,
        context_window=8192,
        tags=["ollama", "8b", "tier2"],
    ),
    # Our benchmark: fastest (38s, 116 tok/s), decent quality at 3B
    ModelConfig(
        name="llama3.2-3b",
        model="llama3.2:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "3b", "tier2"],
    ),
    # LLMStructBench 0.67 at only 1.7B — smallest high scorer
    # (not yet tested in our pipeline)
    # ModelConfig(
    #     name="qwen3-1.7b",
    #     model="qwen3:1.7b",
    #     base_url=OLLAMA_BASE,
    #     tags=["ollama", "1.7b", "tier2"],
    # ),
    ModelConfig(
        name="gemma3-4b",
        model="gemma3:4b",
        base_url=OLLAMA_BASE,
        context_window=8192,
        tags=["ollama", "4b", "tier2"],
    ),
]

# ═══════════════════════════════════════════════════════════════════════════
# Non-LLM extraction models (need custom adapters, not Ollama)
# ═══════════════════════════════════════════════════════════════════════════
#
# GLiNER (~300M) — pip install gliner                          [CD-rrj]
#   87% F1 zero-shot NER, 140x smaller than UniNER, 0.08s inference.
#   Bidirectional encoder, NOT an LLM. Runs on CPU.
#   https://github.com/urchade/GLiNER
#
# NuExtract 2.0-8B — GGUF import to Ollama                    [CD-9zg]
#   Qwen2.5-VL fine-tune, multimodal extraction.
#   mradermacher/NuExtract-2.0-8B-GGUF Q4_K_M (4.5GB)
#
# UniversalNER 7B — GGUF import to Ollama                     [CD-3jl]
#   GPT-3.5 distilled, 43 NER datasets across 9 domains.
#   yuuko-eth/UniNER-7B-all-GGUF

# ═══════════════════════════════════════════════════════════════════════════
# OpenAI / Cloud API models — require OPENAI_API_KEY
# Use as ground truth baseline for scoring local models against.
# ═══════════════════════════════════════════════════════════════════════════

LITELLM_BASE = os.environ.get("LLM_BASE_URL", "http://litellm.talos00/v1")
LITELLM_KEY = os.environ.get("LLM_API_KEY", "")

CLOUD_MODELS = [
    ModelConfig(
        name="gpt-4o-mini",
        model="gpt-4o-mini",
        base_url=LITELLM_BASE,
        structured_method="function_calling",
        api_key=LITELLM_KEY,
        context_window=128000,
        tags=["cloud", "openai", "baseline"],
    ),
    ModelConfig(
        name="gpt-4o",
        model="gpt-4o",
        base_url=LITELLM_BASE,
        structured_method="function_calling",
        api_key=LITELLM_KEY,
        context_window=128000,
        tags=["cloud", "openai", "baseline"],
    ),
    ModelConfig(
        name="claude-sonnet-4",
        model="claude-sonnet-4-20250514",
        base_url=LITELLM_BASE,
        structured_method="function_calling",
        api_key=LITELLM_KEY,
        context_window=200000,
        tags=["cloud", "anthropic", "baseline"],
    ),
    # claude-haiku-3.5 — disabled: LiteLLM deployment unavailable
    # ModelConfig(
    #     name="claude-haiku-3.5",
    #     model="claude-3-5-haiku-20241022",
    #     base_url=LITELLM_BASE,
    #     structured_method="function_calling",
    #     api_key=LITELLM_KEY,
    #     tags=["cloud", "anthropic"],
    # ),
]

# ═══════════════════════════════════════════════════════════════════════════
# All models — used by the multi-model benchmark runner
# ═══════════════════════════════════════════════════════════════════════════

# Local models (no API key needed)
LOCAL_MODELS = EXTRACTION_MODELS + ENCODER_MODELS + TIER1_MODELS + TIER2_MODELS

# All models including cloud (requires OPENAI_API_KEY)
ALL_MODELS = LOCAL_MODELS + CLOUD_MODELS


def get_available_models(tags: list[str] | None = None) -> list[ModelConfig]:
    """Return models filtered by tags (e.g. ["tier1"], ["8b"])."""
    if not tags:
        return ALL_MODELS
    return [m for m in ALL_MODELS if any(t in m.tags for t in tags)]


def get_model_by_name(name: str) -> ModelConfig | None:
    """Look up a model config by its short display name."""
    for m in ALL_MODELS:
        if m.name == name:
            return m
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Ensemble Ground Truth — model panels for consensus voting
# ═══════════════════════════════════════════════════════════════════════════
# Default panels — models most likely to have cached fixtures.
# The ensemble generator gracefully skips models without fixtures.

NER_ENSEMBLE_MODELS = [
    "gliner-large",  # encoder
    "gpt-4o",  # cloud LLM
    "claude-sonnet-4-20250514",  # cloud LLM
    "mistral:latest",  # local LLM
    "gemma3:12b",  # local LLM
]

SPO_ENSEMBLE_MODELS = [
    "claude-sonnet-4-20250514",  # best Anthropic
    "gpt-4o",  # best OpenAI
]


# ═══════════════════════════════════════════════════════════════════════════
# BenchmarkConfig — unified, serializable benchmark parameters
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class BenchmarkConfig:
    """Unified benchmark configuration — serializable to run-config.json."""

    chunk_config: ChunkConfig
    models: list[ModelConfig]
    ner_ensemble: list[str]
    spo_ensemble: list[str]
    ensemble_threshold: str  # "majority"
    exgraph_enabled: bool = True
    timeout_s: int = 300
    audit_log: bool = False
    label: str = ""

    @classmethod
    def from_args(cls, args, models: list[ModelConfig]) -> BenchmarkConfig:
        """Build from argparse namespace."""
        from dagster_io.chunking import ChunkConfig

        chunk_tokens = getattr(args, "chunk_size", None)
        chunk_config = (
            ChunkConfig(model_context_tokens=chunk_tokens * 4 if chunk_tokens else 4096)
            if chunk_tokens
            else ChunkConfig()
        )

        return cls(
            chunk_config=chunk_config,
            models=models,
            ner_ensemble=NER_ENSEMBLE_MODELS,
            spo_ensemble=SPO_ENSEMBLE_MODELS,
            ensemble_threshold="majority",
            exgraph_enabled=getattr(args, "exgraph", False),
            timeout_s=getattr(args, "timeout", 300),
            audit_log=getattr(args, "audit_log", False),
            label=getattr(args, "label", "") or "",
        )

    def to_dict(self) -> dict:
        """Serialize for run-config.json."""
        return {
            "chunk_config": {
                "target_tokens": self.chunk_config.target_tokens,
                "target_chars": self.chunk_config.target_chars,
                "model_context_tokens": self.chunk_config.model_context_tokens,
                "context_fraction": self.chunk_config.context_fraction,
                "strategy": self.chunk_config.strategy,
            },
            "models": [m.name for m in self.models],
            "model_count": len(self.models),
            "ner_ensemble": self.ner_ensemble,
            "spo_ensemble": self.spo_ensemble,
            "ensemble_threshold": self.ensemble_threshold,
            "exgraph_enabled": self.exgraph_enabled,
            "timeout_s": self.timeout_s,
            "audit_log": self.audit_log,
            "label": self.label,
        }
