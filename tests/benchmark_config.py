"""Central configuration for extraction benchmarks.

Defines which models/endpoints to test against. Edit this file to add or
remove models from the benchmark suite.

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
    structured_method: str = "function_calling"  # function_calling | json_mode | json_schema
    api_key: str = "unused"
    max_tokens: int = 4096
    timeout: int = 300
    tags: list[str] = field(default_factory=list)  # for filtering: ["ollama", "vllm", "small", "large"]


# ═══════════════════════════════════════════════════════════════════════════
# Ollama models (tool calling via /v1 endpoint)
# ═══════════════════════════════════════════════════════════════════════════

OLLAMA_BASE = "http://localhost:11434/v1"

OLLAMA_MODELS = [
    ModelConfig(
        name="mistral-7b",
        model="mistral:latest",
        base_url=OLLAMA_BASE,
        structured_method="json_mode",  # tool calling unreliable with Ollama
        tags=["ollama", "small"],
    ),
    ModelConfig(
        name="llama3.2-3b",
        model="llama3.2:latest",
        base_url=OLLAMA_BASE,
        structured_method="json_mode",
        tags=["ollama", "small"],
    ),
    ModelConfig(
        name="qwen2.5-32b",
        model="qwen2.5:32b",
        base_url=OLLAMA_BASE,
        structured_method="json_mode",
        tags=["ollama", "large"],
    ),
    ModelConfig(
        name="llama3.3-70b",
        model="llama3.3:latest",
        base_url=OLLAMA_BASE,
        structured_method="json_mode",
        tags=["ollama", "large"],
    ),
    ModelConfig(
        name="nuextract-3.8b",
        model="nuextract:latest",
        base_url=OLLAMA_BASE,
        structured_method="json_mode",
        tags=["ollama", "small", "extraction-specialist"],
    ),
]

# ═══════════════════════════════════════════════════════════════════════════
# vLLM-MLX models (json_mode — tool calling not properly supported)
# ═══════════════════════════════════════════════════════════════════════════

VLLM_MODELS = [
    ModelConfig(
        name="devstral-24b",
        model="mlx-community/Devstral-Small-2-24B-Instruct-2512-4bit",
        base_url="http://localhost:8000/v1",
        structured_method="json_mode",
        tags=["vllm", "large"],
    ),
    ModelConfig(
        name="deepseek-r1-32b",
        model="mlx-community/DeepSeek-R1-Distill-Qwen-32B-4bit",
        base_url="http://localhost:8001/v1",
        structured_method="json_mode",
        tags=["vllm", "large"],
    ),
    ModelConfig(
        name="qwen3-32b",
        model="mlx-community/Qwen3-32B-4bit",
        base_url="http://localhost:8002/v1",
        structured_method="json_mode",
        tags=["vllm", "large"],
    ),
    ModelConfig(
        name="qwen3-coder-30b",
        model="mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
        base_url="http://localhost:8003/v1",
        structured_method="json_mode",
        tags=["vllm", "large"],
    ),
]

# ═══════════════════════════════════════════════════════════════════════════
# All models — used by the multi-model benchmark runner
# ═══════════════════════════════════════════════════════════════════════════

ALL_MODELS = OLLAMA_MODELS + VLLM_MODELS


def get_available_models(tags: list[str] | None = None) -> list[ModelConfig]:
    """Return models filtered by tags (e.g. ["ollama", "small"])."""
    if not tags:
        return ALL_MODELS
    return [m for m in ALL_MODELS if any(t in m.tags for t in tags)]


def get_model_by_name(name: str) -> ModelConfig | None:
    """Look up a model config by its short display name."""
    for m in ALL_MODELS:
        if m.name == name:
            return m
    return None
