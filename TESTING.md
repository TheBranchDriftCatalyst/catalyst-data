# Testing Guide

This project uses pytest with a cascading fixture pattern. Integration tests run actual pipeline code against real data sources and save output at each stage for downstream tests to consume.

## Quick Reference

```bash
# Unit tests (fast, no API keys needed)
pytest libs/ packages/ -k "not integration and not llm and not slow"

# Media-ingest integration (requires demo_video.mp4)
pytest tests/test_pipeline_integration.py -v -s

# Media-ingest extraction (requires LLM API key)
LLM_MODEL=gpt-4o-mini OPENAI_API_KEY=xxx \
    pytest tests/test_pipeline_integration.py -k "extraction" -v -s

# Congress integration (requires Congress API key)
CONGRESS_API_KEY=xxx DAGSTER_CODE_LOCATION=congress_data \
    pytest packages/congress-data/tests/integration/ -v -s

# Congress extraction (requires both API keys)
CONGRESS_API_KEY=xxx LLM_API_KEY=xxx LLM_MODEL=gpt-4o-mini DAGSTER_CODE_LOCATION=congress_data \
    pytest packages/congress-data/tests/integration/test_pipeline.py -k "gold or full" -v -s
```

## Test Structure

```
tests/
  conftest.py                       # Shared Dagster fixtures, safe env defaults
  test_pipeline_integration.py      # Media-ingest: full pipeline cascade
  test_extraction_benchmark.py      # Media-ingest: ground truth + model comparison
  shared/
    __init__.py
    local_io_manager.py             # Filesystem IO manager for test outputs
    extraction_scoring.py           # Mention/proposition F1 scoring

  fixtures/                         # Cached pipeline outputs (generated, not committed)
    transcription.json              # Step 1: Whisper transcription
    diarization.json                # Step 2: Speaker diarization
    segment_merge.json              # Step 3: Same-speaker merge
    chunks.json                     # Step 4: Speaker-aware chunking
    extraction_{model}.json         # Step 5: LLM extraction (per-model)
    ground_truth_media_ingest.json  # Reviewed ground truth for benchmarking

packages/congress-data/tests/
  integration/
    conftest.py                     # Congress test fixtures + CLI options
    test_pipeline.py                # Full bill pipeline: bronze → silver → gold
    test_extraction_benchmark.py    # Congress: ground truth + model comparison
```

## Integration Test Pipeline

### Media-Ingest Cascade

Each step uses cached fixtures if available. Delete a fixture to regenerate from that stage onward.

```
demo_video.mp4
  → Step 1: Transcription (faster-whisper)      → fixtures/transcription.json
  → Step 2: Diarization (pyannote)               → fixtures/diarization.json
  → Step 3: Segment merge (same-speaker)          → fixtures/segment_merge.json
  → Step 4: Speaker-aware chunking                 → fixtures/chunks.json
  → Step 5: Validated extraction (LangGraph)        → fixtures/extraction_{model}.json
```

Steps 1-4 are model-independent. Step 5 saves a separate fixture per LLM model.

### Congress-Data Cascade

```
Congress.gov API (partition: 119-hres-1)
  → Bronze: bill_detail, bill_actions, bill_cosponsors, bill_text_versions, bill_full_text
  → Silver: bill_document, bill_chunks
  → Gold: bill_mentions, bill_assertions, bill_embeddings
      → .test-output/congress-data/fixtures/extraction_{model}.json
```

## Extraction Benchmarking

Compare extraction quality across different LLM models using F1 scoring.

### Step 1: Generate Extraction Fixtures

Run the pipeline with each model you want to test:

```bash
# Media-ingest
LLM_MODEL=gpt-4o-mini OPENAI_API_KEY=xxx \
    pytest tests/test_pipeline_integration.py -k "extraction" -v -s

LLM_MODEL=gpt-4o OPENAI_API_KEY=xxx \
    pytest tests/test_pipeline_integration.py -k "extraction" -v -s

# Congress-data
CONGRESS_API_KEY=xxx LLM_API_KEY=xxx LLM_MODEL=gpt-4o DAGSTER_CODE_LOCATION=congress_data \
    pytest packages/congress-data/tests/integration/test_pipeline.py -k "full_bill" -v -s
```

Each run creates `fixtures/extraction_{model}.json` with the model's mention and assertion output.

### Step 2: Generate Ground Truth

Two approaches for generating ground truth:

**Option A: Single strong model** (when cloud API is available):
```bash
LLM_MODEL=gpt-4o \
    pytest tests/test_extraction_benchmark.py -k "generate_ground_truth" -v -s
```

**Option B: Ensemble consensus** (recommended — uses existing local model fixtures):
```bash
# Run all local models first, then generate consensus ground truth:
python tests/benchmark_harness.py --local-only
# The harness uses ConsensusVoter to merge top-N model outputs:
# - NER: top 5 models by mention count, majority vote (>=2/5 agree)
# - SPO: top 3 models with assertions, majority vote (>=2/3 agree)
```

The ensemble approach produces stronger ground truth than any single model because
it requires multiple independent models to agree on each entity/proposition.

### Step 3: Review Ground Truth

Ground truth is saved to `.test-output/media-ingest/fixtures/ground_truth_media_ingest.json`.

Review and correct:
- Fix wrong `mention_type` labels
- Fix `span_start`/`span_end` offsets that don't match the text
- Add missed entities, remove false positives
- Set `"manually_reviewed": true` when done

Validate spans: `pytest tests/test_extraction_benchmark.py -k "self_check" -v -s`

### Step 4: Benchmark Models with F1 Scoring

Once ground truth exists, all benchmark runs automatically compute F1 scores:

```bash
# Run benchmarks — F1 scores appear in report when ground truth exists
python tests/benchmark_harness.py --regen

# Or via pytest:
PYTHONPATH=. pytest tests/test_extraction_benchmark.py::TestRunAll -v -s --regen
```

## Scoring Methodology

### Metrics Computed

**Extraction Quality (requires ground truth):**
- **F1 Score** — harmonic mean of precision and recall (0-1, higher = better)
- **Precision** — of predictions, how many were correct? (accuracy of positive predictions)
- **Recall** — of actual entities, how many did the model catch? (completeness)
- **Type Accuracy** — among text-matched entities, fraction with correct entity type
- **Span Accuracy** — fraction where `source[start:end] == entity_text`
- **Hallucination Rate** — 1 - span_accuracy (entities not found in source text)

Both **strict** (text + type must match) and **relaxed** (text only) variants are computed.

**Efficiency Metrics (no ground truth needed):**
- **Tokens/sec** — generation throughput
- **Per-chunk Latency** — duration / chunk_count
- **Quality/Speed Ratio** — F1 / duration (best bang for buck)

**Pipeline Metrics (from MCP validation audit trail):**
- Per-stage call counts (extract, validate, repair)
- Validation verdicts (valid/ambiguous/invalid)
- Repair cycle counts
- MCP error codes (SPAN_MISMATCH, DUPLICATE_SPAN, etc.)

### How F1 Works

F1 = 2 × (precision × recall) / (precision + recall)

The harmonic mean punishes imbalance — a model with 99% precision but 1% recall
gets ~2% F1, not 50%. This prevents gaming by either:
- Always saying "no" (high precision, zero recall)
- Extracting everything (high recall, low precision)

### Benchmark Report Viewer

The React SPA at `/viewer/benchmarks` renders:
- **Overview** — stat cards, performance bar charts, model cards by type
- **Scores** — F1 comparison charts, precision/recall table, efficiency metrics
- **Entities** — matrix showing which models found which entities
- **Propositions** — SPO triple matrix across models
- **Pipeline** — LangGraph stage breakdown with MCP validation stats

```bash
cd packages/media-ingest/viewer-ui && npm run dev
# Open http://localhost:5173/viewer/benchmarks
```

Output example:

```
======================================================================
  Multi-Model Comparison (ground truth: gpt-4o)
======================================================================

  Model                     M-F1 (strict)   M-F1 (relax)    P-F1 (strict)   P-F1 (relax)
  -----------------------------------------------------------------
  gpt-4o                    1.000           1.000           1.000           1.000
  gpt-4o-mini               0.847           0.912           0.723           0.801
  claude-3-5-sonnet          0.891           0.934           0.756           0.834
======================================================================
```

### Scoring Metrics

**Mentions:**
- **Strict F1**: text + mention_type both match
- **Relaxed F1**: text matches (type may differ)
- **Type accuracy**: among text-matched mentions, fraction with correct type
- **Span accuracy**: fraction of spans where `source_text[start:end] == text`

**Propositions:**
- **Strict F1**: subject + predicate + object all match (normalized)
- **Relaxed F1**: subject + object match, predicate ignored

## Environment Variables

| Variable | Required For | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | LLM extraction, embeddings | — |
| `LLM_API_KEY` | LLM extraction (alias) | — |
| `LLM_MODEL` | Model selection | `gpt-4o-mini` |
| `LLM_BASE_URL` | Custom LLM endpoint (vLLM, Ollama, etc.) | OpenAI |
| `CONGRESS_API_KEY` | Congress.gov API access | — |
| `HF_TOKEN` | Pyannote diarization models | — |
| `DAGSTER_CODE_LOCATION` | Congress tests | `congress_data` |
| `PROMPT_REGISTRY_DIR` | Prompt directory (use `k8s/shared/prompts` for all prompts incl. repair) | auto-detected |

## Model Configuration

All benchmark models are defined in **`tests/benchmark_config.py`**. Edit this file to add/remove models:

```python
from tests.benchmark_config import ALL_MODELS, OLLAMA_MODELS, VLLM_MODELS
```

Each model specifies: name, model ID, base URL, structured output method, and tags.

### Structured Output Methods

- **`function_calling`** (default): OpenAI tool/function calling. Works with OpenAI, Azure.
- **`json_mode`**: Forces JSON output via `response_format`. Works with Ollama, vLLM-MLX, nuextract, and any model that can produce JSON but doesn't support tool calling well.
- **`json_schema`**: OpenAI strict JSON schema mode.

Local models (Ollama, vLLM-MLX) generally work best with `json_mode`. The benchmark config already sets this.

### Run All Configured Models

```bash
PYTHONPATH=. pytest tests/test_extraction_benchmark.py::TestRunAll -v -s
```

This iterates over all models in `benchmark_config.py`, checks endpoint availability, runs extraction with MCP validation + repair, and prints a comparison table.

## Adding New Models

To benchmark a new model:

1. Add a `ModelConfig` entry to `tests/benchmark_config.py`
2. Run `TestRunAll` or set env vars manually:
   ```bash
   LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=your-model \
       LLM_STRUCTURED_METHOD=json_mode \
       pytest tests/test_pipeline_integration.py -k extraction -v -s
   ```
3. Run `compare_all` to see how it stacks up

Works with any OpenAI-compatible API: OpenAI, Azure, vLLM, Ollama, LiteLLM proxy, etc.

## Tips

- Delete a fixture file to force regeneration from that stage
- Chunk fixtures (Steps 1-4) are model-independent — only extraction varies per model
- Use `-k "not llm"` to skip LLM-dependent tests
- Use `--partition 119-hr-1` to test with a larger bill (congress)
- Extraction uses `extract_validated()` from `dagster_io.extraction` — the exact same code path as production
