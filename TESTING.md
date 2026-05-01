# Testing Guide

This project uses pytest with a cascading fixture pattern. Integration tests run actual pipeline code against real data sources and save output at each stage for downstream tests to consume.

## Quick Start

```bash
# Full benchmark (run all models, generate ground truth, score, report):
PYTHONPATH=. python tests/benchmark_harness.py --full --exgraph

# Interactive mode (guided menu):
PYTHONPATH=. python tests/benchmark_harness.py

# Unit tests (fast, no API keys needed):
pytest libs/ packages/ -k "not integration and not llm and not slow"
```

## Quick Reference

```bash
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

## Directory Structure

There are two distinct categories of test data:

**True Fixtures** (checked into git, never deleted by `--regen` or `bench:clean`):
```
tests/fixtures/
    benchmark_chunks.json    # curated benchmark subset (4 representative chunks)
    demo_video.mp4           # source media for pipeline integration tests
    model_cache/             # local model weights cache
```

**Cached Artifacts** (generated, gitignored, regenerable):
```
.test-output/media-ingest/
    pipeline-cache/              # expensive pipeline outputs cached between runs
        transcription.json       # Step 1: Whisper transcription
        diarization.json         # Step 2: Speaker diarization
        segment_merge.json       # Step 3: Same-speaker merge
        chunks.json              # Step 4: Speaker-aware chunking
        benchmark_chunks.json    # copy of curated subset
    ground-truth/                # ground truth versions (independent of runs)
        ensemble-5model.json     # named, versioned
        active.json              # currently used for scoring
    runs/                        # timestamped benchmark runs
        2026-04-29-exgraph-v2/
            extractions/         # extraction_model.json per model
            audit-logs/          # structured audit logs per model
            benchmark-report.json
            run-config.json
        latest -> ...            # symlink to most recent run
    fixtures/                    # legacy flat layout (backward compat)
        extraction_*.json        # LLM extraction outputs per model
        ground_truth_*.json      # ground truth (synced with ground-truth/)
    benchmark-report.json        # top-level copy for viewer SPA
    audit-logs/                  # top-level copy for viewer SPA
```

## Test Structure

```
tests/
    conftest.py                     # shared Dagster fixtures, safe env defaults
    test_pipeline_integration.py    # media-ingest: full pipeline cascade
    test_extraction_benchmark.py    # media-ingest: ground truth + model scoring
    benchmark_harness.py            # CLI entry point (interactive + flags)
    benchmark_config.py             # model registry + ensemble model panels
    shared/
        __init__.py
        store.py                    # BenchmarkStore + RunStore (centralized I/O)
        ground_truth.py             # ensemble consensus logic
        report.py                   # report builder for viewer SPA
        extraction_scoring.py       # mention/proposition F1 scoring
        local_io_manager.py         # filesystem IO manager for test outputs
    fixtures/
        benchmark_chunks.json       # true fixture (curated, checked into git)

packages/congress-data/tests/
    integration/
        conftest.py                 # congress test fixtures + CLI options
        test_pipeline.py            # full bill pipeline: bronze -> silver -> gold
        test_extraction_benchmark.py
```

## Integration Test Pipeline

### Media-Ingest Cascade

Each step uses cached artifacts if available. Delete an artifact to regenerate from that stage onward.

```
demo_video.mp4
    -> Step 1: Transcription (faster-whisper)      -> pipeline-cache/transcription.json
    -> Step 2: Diarization (pyannote)               -> pipeline-cache/diarization.json
    -> Step 3: Segment merge (same-speaker)          -> pipeline-cache/segment_merge.json
    -> Step 4: Speaker-aware chunking                 -> pipeline-cache/chunks.json
    -> Step 5: Validated extraction (LangGraph)        -> fixtures/extraction_{model}.json
```

Steps 1-4 are model-independent. Step 5 saves a separate artifact per LLM model.

**Design principle**: The benchmark harness (`benchmark_harness.py`) and the pipeline integration tests (`test_pipeline_integration.py`) share a 1:1 mapping to the Dagster production pipeline. Both use `extract_validated()` — the exact same code path as production Dagster assets. The benchmark harness runs it across 15 models in subprocess; the integration test runs it once for the current `LLM_MODEL`. Cached artifacts are shared — an extraction generated by either path can be consumed by the other. This ensures benchmarks measure the real pipeline, not a test-only approximation.

### Congress-Data Cascade

```
Congress.gov API (partition: 119-hres-1)
    -> Bronze: bill_detail, bill_actions, bill_cosponsors, bill_text_versions, bill_full_text
    -> Silver: bill_document, bill_chunks
    -> Gold: bill_mentions, bill_assertions, bill_embeddings
        -> .test-output/congress-data/fixtures/extraction_{model}.json
```

## Extraction Benchmarking

See [BENCHMARK.md](BENCHMARK.md) for the full extraction benchmark reference, including:
- 4-phase flow (model runs, ensemble GT, scoring, report)
- Model registry (15 models), ground truth management, scoring methodology
- Viewer UI (7 tabs), CLI reference, task commands
- Artifact management, tiered clean system, provenance tracking

## Environment Variables

| Variable | Required For | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | LLM extraction, embeddings | -- |
| `LLM_API_KEY` | LLM extraction (alias) | -- |
| `LLM_MODEL` | Model selection | `gpt-4o-mini` |
| `LLM_BASE_URL` | Custom LLM endpoint (vLLM, Ollama, etc.) | OpenAI |
| `CONGRESS_API_KEY` | Congress.gov API access | -- |
| `HF_TOKEN` | Pyannote diarization models | -- |
| `DAGSTER_CODE_LOCATION` | Congress tests | `congress_data` |
| `PROMPT_REGISTRY_DIR` | Prompt directory | auto-detected |
| `EXGRAPH_ENABLED` | Enable exgraph v2 pipeline | `false` |
| `TEST_OUTPUT_ROOT` | Override test output location | `.test-output` |

## Tips

- Delete a cached artifact file to force regeneration from that stage
- Pipeline cache (Steps 1-4) is model-independent -- only extraction varies per model
- Use `-k "not llm"` to skip LLM-dependent tests
- Use `--partition 119-hr-1` to test with a larger bill (congress)
- Extraction uses `extract_validated()` from `dagster_io.extraction` -- the exact same code path as production
- Each benchmark run is preserved in `runs/` -- old runs are never overwritten
- The `latest` symlink always points to the most recent run
