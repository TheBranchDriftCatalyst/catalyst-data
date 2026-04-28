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

Use a strong model's output as the starting point for ground truth:

```bash
# Media-ingest — uses extraction_{model}.json as the reference
LLM_MODEL=gpt-4o \
    pytest tests/test_extraction_benchmark.py -k "generate_ground_truth" -v -s

# Congress-data
pytest packages/congress-data/tests/integration/test_extraction_benchmark.py \
    -k "generate_ground_truth" -v -s --output-dir .test-output/congress-data
```

This creates a ground truth fixture with `"manually_reviewed": false`.

### Step 3: Review Ground Truth

Open the ground truth JSON and manually correct any errors:
- Fix wrong `mention_type` labels
- Fix `span_start`/`span_end` offsets that don't match the text
- Add missed entities
- Remove false positives
- Set `"manually_reviewed": true` when done

Run the self-check to validate spans:

```bash
pytest tests/test_extraction_benchmark.py -k "self_check" -v -s
```

### Step 4: Benchmark Models

Score any model's output against the reviewed ground truth:

```bash
# Score a specific model
LLM_MODEL=gpt-4o-mini \
    pytest tests/test_extraction_benchmark.py -k "benchmark" -v -s

# Compare ALL models at once
pytest tests/test_extraction_benchmark.py -k "compare_all" -v -s
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
