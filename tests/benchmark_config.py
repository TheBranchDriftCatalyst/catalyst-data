"""Central configuration for extraction benchmarks.

Defines which models/endpoints to test against. Edit this file to add or
remove models from the benchmark suite.

Target: ≤12B params for local inference on Apple Silicon.
Focus: structured entity extraction (NER) and proposition extraction (SPO).

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

# ── Extraction specialists (purpose-built for NER) ────────────────────────

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
    # NuExtract 2.0-8B — import via:
    #   curl -L -o /tmp/NuExtract-2.0-8B.Q4_K_M.gguf \
    #     "https://huggingface.co/mradermacher/NuExtract-2.0-8B-GGUF/resolve/main/NuExtract-2.0-8B.Q4_K_M.gguf"
    #   ollama create nuextract2 -f Modelfile.nuextract2
    # ModelConfig(
    #     name="nuextract-2.0-8b",
    #     model="nuextract2:latest",
    #     base_url=OLLAMA_BASE,
    #     tags=["ollama", "extraction-specialist", "8b"],
    # ),
]

# ── General-purpose LLMs (~4B) ────────────────────────────────────────────

SMALL_MODELS = [
    ModelConfig(
        name="llama3.2-3b",
        model="llama3.2:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "3b"],
    ),
    ModelConfig(
        name="qwen3-4b",
        model="qwen3:4b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "4b"],
    ),
    ModelConfig(
        name="gemma3-4b",
        model="gemma3:4b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "4b"],
    ),
    ModelConfig(
        name="phi4-mini",
        model="phi4-mini:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "3.8b"],
    ),
]

# ── General-purpose LLMs (~7-8B) ──────────────────────────────────────────

MEDIUM_MODELS = [
    ModelConfig(
        name="mistral-7b",
        model="mistral:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "7b"],
    ),
    ModelConfig(
        name="qwen2.5-7b",
        model="qwen2.5:7b-instruct",
        base_url=OLLAMA_BASE,
        tags=["ollama", "7b"],
    ),
    ModelConfig(
        name="llama3.1-8b",
        model="llama3.1:8b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "8b"],
    ),
    ModelConfig(
        name="qwen3-8b",
        model="qwen3:8b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "8b"],
    ),
    ModelConfig(
        name="hermes3-8b",
        model="hermes3:8b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "8b"],
    ),
    ModelConfig(
        name="dolphin3-8b",
        model="dolphin3:8b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "8b"],
    ),
    ModelConfig(
        name="granite3.1-8b",
        model="granite3.1-dense:8b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "8b"],
    ),
]

# ── General-purpose LLMs (~12B) ───────────────────────────────────────────

LARGE_MODELS = [
    ModelConfig(
        name="mistral-nemo-12b",
        model="mistral-nemo:latest",
        base_url=OLLAMA_BASE,
        tags=["ollama", "12b"],
    ),
    ModelConfig(
        name="gemma3-12b",
        model="gemma3:12b",
        base_url=OLLAMA_BASE,
        tags=["ollama", "12b"],
    ),
]

# ═══════════════════════════════════════════════════════════════════════════
# Non-LLM extraction models (need custom adapters, not Ollama)
# ═══════════════════════════════════════════════════════════════════════════
#
# These are encoder-based models that run via Python libraries, not serving APIs.
# They need custom adapter integration (like the NuExtract adapter).
#
# GLiNER (~300M) — pip install gliner
#   Zero-shot NER via bidirectional transformer. Competitive with ChatGPT.
#   https://github.com/urchade/GLiNER
#
# NuNER (125M) — HuggingFace encoder
#   56x smaller than UniversalNER, similar fine-tuning performance.
#   https://arxiv.org/html/2402.15343v1
#
# UniversalNER (7B GGUF available)
#   GPT-3.5 distilled, 43 NER datasets. yuuko-eth/UniNER-7B-all-GGUF
#   Could be imported to Ollama like NuExtract 1.5.

# ═══════════════════════════════════════════════════════════════════════════
# All models — used by the multi-model benchmark runner
# ═══════════════════════════════════════════════════════════════════════════

ALL_MODELS = EXTRACTION_MODELS + SMALL_MODELS + MEDIUM_MODELS + LARGE_MODELS


def get_available_models(tags: list[str] | None = None) -> list[ModelConfig]:
    """Return models filtered by tags (e.g. ["ollama", "8b"])."""
    if not tags:
        return ALL_MODELS
    return [m for m in ALL_MODELS if any(t in m.tags for t in tags)]


def get_model_by_name(name: str) -> ModelConfig | None:
    """Look up a model config by its short display name."""
    for m in ALL_MODELS:
        if m.name == name:
            return m
    return None
