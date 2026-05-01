# Testing Guide

This project uses pytest with a cascading fixture pattern. Integration tests run actual pipeline code against real data sources and save output at each stage for downstream tests to consume.

## Quick Start

```bash
# Single-video flow — pre-warm the demo_video.mp4 audio cache once:
WHISPER_BACKEND=mlx-whisper task bench:pipeline:warm   # Apple Silicon (Metal)
task bench:pipeline:warm                                # other platforms (faster-whisper CPU)

# Multi-video flow — populate the per-doc-id cache for every video in the manifest:
HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen

# Full benchmark (single-video, run all models, generate ground truth, score, report):
PYTHONPATH=. python tests/benchmark_harness.py --full --exgraph

# Multi-video benchmark (per-doc-id extractions for every manifest video):
PYTHONPATH=. python tests/benchmark_harness.py --all-videos --models gliner-medium

# Interactive mode (guided menu):
PYTHONPATH=. python tests/benchmark_harness.py

# Unit tests (fast, no API keys needed):
pytest libs/ packages/ -k "not integration and not llm and not slow"
```

> Run `task bench:pipeline:warm` *before* `task bench` so you can see the slow
> Whisper + pyannote work happen live. The harness's `_run_model` subprocess
> captures stdout, so cold transcription/diarization inside the harness shows
> up as a long silence between `RUNNING <model>` and `OK`.

## Quick Reference

```bash
# Media-ingest integration (requires tests/fixtures/media-ingest/demo_video.mp4)
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
    media-ingest/
        audio_manifest.yaml          # 7 source videos -> stable doc_ids
        demo_video.mp4               # smallest fixture; default for single-video integration tests
        <other source videos>.mp4    # gitignored when present (compressed via scripts/compress_fixtures.py)
        benchmark_chunks.json        # merged curated chunks across all manifest videos
        per_video_chunks/<doc_id>/
            benchmark_chunks.json    # per-video curated chunk subset (source of truth)
    congress-data/
        benchmark_chunks.json        # curated bill chunks (4 chunks)
    open-leaks/
        benchmark_chunks.json        # curated leak chunks (3 chunks)
    benchmark_chunks.json            # legacy single-file fallback
    model_cache/                     # local model weights cache
```

**Cached Artifacts** (generated, gitignored, regenerable):
```
.test-output/media-ingest/
    pipeline-cache/                  # expensive audio-model outputs (slow stages only)
        <doc_id>/                    # one subdir per video in audio_manifest.yaml
            0_transcription.json     # Whisper output for this video
            1_diarization.json       # pyannote output for this video
        # Flat pipeline-cache/0_transcription.json + 1_diarization.json
        # remain readable as the single-video fallback for legacy
        # `task bench:pipeline:warm` runs (doc_id=None path).
    ground-truth/                    # ground truth versions (independent of runs)
        ensemble-12model.json        # named, versioned
        active.json                  # currently used for scoring
    extractions/                     # cross-run cached LLM extractions (single-video)
        extraction_<model>.json
    runs/                            # timestamped benchmark runs
        2026-04-29-exgraph-v2/
            extractions/             # single-video flow
                extraction_<model>.json
                <doc_id>/            # multi-video (--all-videos) per-doc-id files
                    extraction_<model>.json
            audit-logs/              # structured audit logs per model
            benchmark-report.json
            run-config.json
        latest -> ...                # symlink to most recent run
    benchmark-report.json            # top-level copy for viewer SPA
    audit-logs/                      # top-level copy for viewer SPA
```

The **stage-prefix on filenames** (`0_transcription.json`, `1_diarization.json`)
reflects pipeline execution order — an `ls` of pipeline-cache shows what runs
in what order. Only the two slow audio-model stages are cached; segment-merge
and chunking are millisecond-fast pure-Python passes.

The **per-doc-id subdir layout** (`pipeline-cache/<doc_id>/`,
`extractions/<doc_id>/`) lets a multi-video run keep each video's artifacts
isolated. `BenchmarkStore.{load,save}_pipeline_artifact(name, doc_id=<slug>)`
and `RunStore.{load,save,list}_extraction(model, doc_id=<slug>)` route to the
subdir; pass `doc_id=None` to read/write the flat single-video paths
(see `tests/shared/store.py:298-336`).

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

```
demo_video.mp4
    -> Step 1: Transcription                       -> pipeline-cache/0_transcription.json  [CACHED]
       (production backend dispatcher: faster-whisper / openvino / mlx-whisper)
    -> Step 2: Diarization (pyannote, MPS/CUDA/CPU auto)
                                                   -> pipeline-cache/1_diarization.json    [CACHED]
    -> Step 3: Segment merge (same-speaker, gap_threshold_s=7.0)                          [recomputed]
    -> Step 4: Speaker-aware chunking
                                                   ChunkingResource.chunk_speaker_segments [recomputed]
    -> Step 5: Validated extraction (LangGraph)    -> extractions/extraction_<model>.json
```

Only the **two slow audio-model stages** (Whisper, pyannote) are cached. Steps
3 and 4 are fast Python passes that run from cached transcription+diarization
on every benchmark invocation — this means iterating on the chunker
(`MAX_CHUNK_CHARS`, pause threshold, etc.) doesn't require regenerating any
audio work.

**Design principle**: the integration test fixture and the production
`media_transcriptions` Dagster asset both call `media_ingest.assets.transcription._select_backend(MediaIngestConfig)`.
This means the test on your Mac with `WHISPER_BACKEND=mlx-whisper` exercises
the same code path you'd deploy on `mac-node`, eliminating dev/prod
deviation. Same property for chunking: both call
`ChunkingResource.chunk_speaker_segments(...)` so test and production share
the chunker logic, including provenance fields (`chunk_id`, `content_hash`).

The benchmark harness (`benchmark_harness.py`) and the pipeline integration
tests share `extract_validated()` from `dagster_io.extraction` — the same
code path as production Dagster assets. The harness runs it across N models
in subprocess; the integration test runs it once for the current `LLM_MODEL`.
Cached artifacts are shared: an extraction generated by either path can be
consumed by the other.

### Multi-Video Workflow

The `tests/fixtures/media-ingest/audio_manifest.yaml` manifest maps a list of
source videos to stable `doc_id` slugs. Each entry drives a per-doc-id branch
of the pipeline cache so multiple videos can coexist without stomping on each
other.

```yaml
# tests/fixtures/media-ingest/audio_manifest.yaml
videos:
  - doc_id: demo-video
    file: 'demo_video.mp4'
    title: 'demo_video'
  - doc_id: inside-the-aipac-pipeline
    file: 'Inside The AIPAC Pipeline.mp4'
    title: 'Inside The AIPAC Pipeline'
  # ...
```

Three regen scripts under `scripts/` operate on the manifest. They run in
order; later scripts depend on the artifacts produced by earlier ones:

| Script | Wraps | Reads | Writes | When to run |
|--------|-------|-------|--------|-------------|
| `compress_fixtures.py` | `media_ingest.assets.transcode._transcode_video` | `*.mp4` source files | replaces `*.mp4` in place | After dropping new source videos into the fixture dir; brings them under git-friendly size |
| `regen_audio_fixtures.py` | `_select_backend(MediaIngestConfig)` + `_run_diarization` | `*.mp4` + manifest | `pipeline-cache/<doc_id>/0_transcription.json`, `1_diarization.json` | After compressing fixtures, or when changing `WHISPER_BACKEND` / `MLX_MODEL_ID` |
| `regen_benchmark_chunks.py` | `ChunkingResource.chunk_speaker_segments` | cached diarization (per-doc-id) | `tests/fixtures/media-ingest/per_video_chunks/<doc_id>/benchmark_chunks.json` + merged `benchmark_chunks.json` | After audio regen, or when changing chunker config (`CHUNK_SIZE`, pause threshold) |
| `bench_extract_per_video.py` | `extract_validated()` | per-doc-id chunks | `runs/<run-id>/extractions/<doc_id>/extraction_<model>.json` (+ flat aggregate roll-up) | Invoked as a subprocess by `benchmark_harness.py --all-videos`; not run directly |

Taskfile shortcuts:

```bash
HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen
HF_TOKEN=hf_xxx task bench:fixtures:regen -- --only demo-video,inside-the-aipac-pipeline
HF_TOKEN=hf_xxx task bench:fixtures:regen -- --force  # bypass per-video cache check

task bench:chunks:regen                                  # all videos
task bench:chunks:regen -- --only saagar-x-joe-kent      # narrow
```

Both scripts are idempotent — `regen_audio_fixtures.py` skips a `doc_id` whose
two cache files already exist unless `--force` is set, and
`regen_benchmark_chunks.py` always recomputes from cached diarization since
the chunker is fast.

#### Multi-video benchmark

```bash
PYTHONPATH=. python tests/benchmark_harness.py --all-videos --models gliner-medium
```

`--all-videos` swaps the harness's per-model subprocess from the single-video
pytest path to `scripts/bench_extract_per_video.py`, which iterates the
manifest. For each video:

1. Loads cached diarization at `pipeline-cache/<doc_id>/1_diarization.json`
2. Runs `ChunkingResource.chunk_speaker_segments(...)` to produce `TextChunk`s
3. Runs `extract_validated()` against those chunks
4. Saves per-doc-id extraction at
   `runs/<run-id>/extractions/<doc_id>/extraction_<model>.json`
5. Writes a flat aggregate roll-up at
   `runs/<run-id>/extractions/extraction_<model>.json` containing per-video
   stats (chunk count, mention count, duration) but with empty `mentions` /
   `assertions` arrays — the per-doc-id files are the source of truth

If a `doc_id`'s audio cache is missing, that video is skipped with a warning
and the run continues. Use `BENCH_ONLY_DOC_IDS=demo-video,...` (env, set by
the harness when narrowing) to restrict the script to a subset.

**What's not yet wired up**: ensemble ground truth and F1 scoring still
operate single-video. Per-(model, video) GT generation, per-pair scoring,
and a per-video viewer breakdown are tracked under beads task **CD-vfiq**
(phases A-E). Today's `--all-videos` run produces extraction artifacts that
can be inspected manually but does not yet produce a per-video F1 table.

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
| `WHISPER_BACKEND` | Transcription backend (`faster-whisper` \| `openvino` \| `mlx-whisper`) | `faster-whisper` (test) / `openvino` (prod) |
| `WHISPER_MODEL` | faster-whisper model name (test fixture) | `base` |
| `WHISPER_DEVICE` | faster-whisper device | `cpu` |
| `WHISPER_COMPUTE_TYPE` | faster-whisper compute type | `int8` |
| `MLX_MODEL_ID` | mlx-whisper HF model id | `mlx-community/whisper-base-mlx` (test) / `mlx-community/whisper-large-v3-mlx` (prod) |
| `CHUNK_SIZE` | Default chunker max chars (audio + text) | `1000` |
| `CHUNK_OVERLAP` | Default chunker overlap | `200` |
| `DAGSTER_CODE_LOCATION` | Congress tests | `congress_data` |
| `PROMPT_REGISTRY_DIR` | Prompt directory | auto-detected |
| `EXGRAPH_ENABLED` | Enable exgraph v2 pipeline | `false` |
| `TEST_OUTPUT_ROOT` | Override test output location | `.test-output` |
| `BENCH_ONLY_DOC_IDS` | Restrict `--all-videos` extraction to a subset (comma-separated `doc_id`s); set by harness when narrowing | -- |
| `SAVE_AUDIT_LOG` | Persist per-video audit events when `--audit-log` is set on the harness | `""` (off) |
| `CATALYST_TELEMETRY` | Force-enable OTEL metrics + tracing export from CLI scripts. Default off outside Dagster — dev tooling stays silent and doesn't try to reach `alloy.monitoring.svc.cluster.local`. Set to `1` / `true` / `yes` / `on` to opt in. Inside Dagster (DAGSTER_RUN_ID/DAGSTER_HOME set) telemetry initializes automatically regardless. | `""` (auto: on inside Dagster, off otherwise) |

### Choosing a Whisper backend

| Hardware | Recommended `WHISPER_BACKEND` | Install |
|---|---|---|
| Apple Silicon (M1/M2/M3) | `mlx-whisper` (Metal) | `pip install -e 'packages/media-ingest[mlx]'` |
| Intel GPU | `openvino` | `pip install -e 'packages/media-ingest[openvino]'` |
| CUDA / CPU / CI | `faster-whisper` | always installed |

All three backends produce the same canonical output schema (`segments[]` with
word timestamps, `language`, `duration_s`). The test fixture and the production
asset call `media_ingest.assets.transcription._select_backend(config)` — pick
one place, configure once.

## Tips

- Delete a cached artifact file (e.g. `pipeline-cache/<doc_id>/0_transcription.json`) to force regeneration from that stage
- Pipeline cache (Steps 1-4) is model-independent — only extraction varies per model
- Use `-k "not llm"` to skip LLM-dependent tests
- Use `--partition 119-hr-1` to test with a larger bill (congress)
- Extraction uses `extract_validated()` from `dagster_io.extraction` — the exact same code path as production
- Each benchmark run is preserved in `runs/` — old runs are never overwritten
- The `latest` symlink always points to the most recent run
- Add a new benchmark video by dropping the `.mp4` into `tests/fixtures/media-ingest/`, appending an entry to `audio_manifest.yaml`, then running `task bench:fixtures:regen` followed by `task bench:chunks:regen`
