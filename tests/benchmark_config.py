"""Central configuration for extraction benchmarks.

Defines which models/endpoints to test against. Edit this file to add or
remove models from the benchmark suite.

Constraint: ≤18B params for local inference on Apple Silicon.

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
# Ollama models — all ≤18B params, json_mode for structured output
# ═══════════════════════════════════════════════════════════════════════════

OLLAMA_BASE = "http://localhost:11434/v1"

OLLAMA_MODELS = [
    ModelConfig(
        name="nuextract-3.8b",
        model="nuextract:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "extraction-specialist"],
    ),
    ModelConfig(
        name="mistral-7b",
        model="mistral:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama"],
    ),
    ModelConfig(
        name="qwen2.5-7b",
        model="qwen2.5:7b-instruct",
        base_url=OLLAMA_BASE,
        tags=["ollama"],
    ),
    ModelConfig(
        name="llama3.1-8b",
        model="llama3.1:8b",
        base_url=OLLAMA_BASE,
        tags=["ollama"],
    ),
    ModelConfig(
        name="mistral-nemo-12b",
        model="mistral-nemo:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama"],
    ),
    ModelConfig(
        name="llama3.2-3b",
        model="llama3.2:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama"],
    ),
]

# ═══════════════════════════════════════════════════════════════════════════
# All models — used by the multi-model benchmark runner
# ═══════════════════════════════════════════════════════════════════════════

ALL_MODELS = OLLAMA_MODELS


def get_available_models(tags: list[str] | None = None) -> list[ModelConfig]:
    """Return models filtered by tags (e.g. ["ollama"])."""
    if not tags:
        return ALL_MODELS
    return [m for m in ALL_MODELS if any(t in m.tags for t in tags)]


def get_model_by_name(name: str) -> ModelConfig | None:
    """Look up a model config by its short display name."""
    for m in ALL_MODELS:
        if m.name == name:
            return m
    return None
