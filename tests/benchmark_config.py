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

from dataclasses import dataclass, field


@dataclass
class ModelConfig:
    """Configuration for a single model to benchmark."""

    name: str  # short display name for reports
    model: str  # model ID passed to the API
    base_url: str  # OpenAI-compatible API base URL
    structured_method: str = "json_mode"  # json_mode works best for local models
    api_key: str = "unused"
    max_tokens: int = 4096
    timeout: int = 600
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
    ModelConfig(
        name="nuextract-3.8b",
        model="nuextract:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "extraction-specialist", "3.8b"],
    ),
    # NuExtract 2.0-8B — needs GGUF import (see CD-9zg)
    # GLiNER 300M — needs pip adapter, not Ollama (see CD-rrj)
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
        tags=["ollama", "12b", "tier1"],
    ),
    # LLMStructBench 0.67 — top scorer at 7B
    ModelConfig(
        name="deepseek-r1-7b",
        model="deepseek-r1:7b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "7b", "tier1"],
    ),
    # LLMStructBench 0.66 — best 3.8B model, punches above weight
    ModelConfig(
        name="phi4-mini",
        model="phi4-mini:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "3.8b", "tier1"],
    ),
    # LLMStructBench 0.65 — same score at 4B as 8B (remarkable efficiency)
    ModelConfig(
        name="qwen3-4b",
        model="qwen3:4b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "4b", "tier1"],
    ),
    # LLMStructBench 0.65
    ModelConfig(
        name="qwen3-8b",
        model="qwen3:8b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "8b", "tier1"],
    ),
]

# ── Tier 2: Strong empirical performers (our benchmark) ───────────────────
# Not in LLMStructBench but performed well in our extraction pipeline tests.

TIER2_MODELS = [
    # Our benchmark: 23 mentions (best recall), 72s, 3 retries
    ModelConfig(
        name="mistral-7b",
        model="mistral:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "7b", "tier2"],
    ),
    # Our benchmark: 22 mentions, 12 assertions (best balanced), 95s
    ModelConfig(
        name="qwen2.5-7b",
        model="qwen2.5:7b-instruct",
        base_url=OLLAMA_BASE,
        tags=["ollama", "7b", "tier2"],
    ),
    # Our benchmark: 17 mentions, 29 assertions (best SPO), 88s
    ModelConfig(
        name="llama3.1-8b",
        model="llama3.1:8b",
        base_url=OLLAMA_BASE,
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
# All models — used by the multi-model benchmark runner
# ═══════════════════════════════════════════════════════════════════════════

ALL_MODELS = EXTRACTION_MODELS + TIER1_MODELS + TIER2_MODELS


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
