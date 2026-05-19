# Testing Guide

This project uses pytest with a cascading fixture pattern. Integration tests run actual pipeline code against real data sources and save output at each stage for downstream tests to consume.

## Quick Start

```bash
# First-time setup: pull API keys + IO config from the catalyst-cluster k8s
# secrets and write per-domain .envrc files (chmod 600, all gitignored).
# Idempotent — re-run any time the cluster secrets rotate.
./scripts/ops/pull-dev-secrets.sh
direnv allow                          # at the repo root, then in each package

# Run dagster dev with all 3 code locations using LocalJsonIOManager —
# materialize anything in the UI without S3. Outputs land at
# .test-output/<domain>/... matching the integration test layout.
task dev                              # http://127.0.0.1:3000

# Single-video flow — pre-warm the demo_video.mp4 audio cache once:
WHISPER_BACKEND=mlx-whisper task bench:pipeline:warm   # Apple Silicon (Metal)
task bench:pipeline:warm                                # other platforms (faster-whisper CPU)

# Multi-video flow — populate the per-doc-id cache for every video in the manifest:
HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen

# Materialize *_chunks Dagster assets across all 3 domains (writes to the
# medallion tree at .test-output/<domain>/<layer>/.../*_chunks/.../data.jsonl):
task bench:chunks:regen                            # all 3 domains
task bench:chunks:regen:media                      # media_chunks only
task bench:chunks:regen:congress                   # bill_chunks (needs CONGRESS_API_KEY)
task bench:chunks:regen:leaks                      # leak_chunks (no creds)

# Full benchmark (single-video, run all models, generate ground truth, score, report):
PYTHONPATH=. python tests/benchmark_harness.py --full

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
# Media-ingest integration — transcription / diarization / segment_merge
# (requires packages/media-ingest/tests/fixtures/demo_video.mp4)
pytest packages/media-ingest/tests/integration/test_pipeline_integration.py -v -s

# Media-ingest chunks materialization (CPU-only — pre-seeds segment_merge
# from cached diarization, materializes media_chunks via LocalJsonIOManager)
pytest packages/media-ingest/tests/integration/test_chunks_cpu.py -v -s

# Cross-domain LLM extraction tests (consumes load_chunks() across all
# 3 domains; requires the *_chunks asset to have been materialized first)
LLM_MODEL=gpt-4o-mini OPENAI_API_KEY=xxx \
    pytest tests/test_extraction_e2e.py -v -s

# Congress integration (requires Congress API key)
CONGRESS_API_KEY=xxx DAGSTER_CODE_LOCATION=congress_data \
    pytest packages/congress-data/tests/integration/ -v -s

# Congress extraction (requires both API keys)
CONGRESS_API_KEY=xxx LLM_API_KEY=xxx LLM_MODEL=gpt-4o-mini DAGSTER_CODE_LOCATION=congress_data \
    pytest packages/congress-data/tests/integration/test_pipeline.py -k "gold or full" -v -s

# Open-leaks chunks materialization (no creds needed)
DAGSTER_CODE_LOCATION=open_leaks \
    pytest packages/open-leaks/tests/integration/test_chunks.py -v -s
```

## Directory Structure

Inputs live in domains, outputs bubble up. Each domain owns its private
fixtures (manifests, source files); each domain's chunks asset writes to a
shared medallion tree under `.test-output/`. The cross-domain harness reads
from that tree via a single glob — it never touches per-domain inputs.

**Domain-private fixtures** (checked into git, never deleted by `bench:clean`):
```
packages/media-ingest/tests/fixtures/
    audio_manifest.yaml              # 7 source videos -> stable doc_ids
    demo_video.mp4                   # smallest fixture; default for single-video integration tests
    <other source videos>.mp4        # gitignored when present (compressed via scripts/fixtures/compress_fixtures.py)

packages/congress-data/tests/fixtures/
    bill_manifest.yaml               # bill_ids the integration test materializes

packages/open-leaks/tests/fixtures/
    <leak source files>              # input documents for leak_chunks
```

**Medallion outputs** (generated, gitignored, regenerable — `task bench:chunks:regen`):
```
.test-output/<domain>/<layer>/<code_loc>/<group>/<asset>/[<partition>/]data.jsonl

  media-ingest/gold/media_ingest/media/media_chunks/<doc_id>/data.jsonl
  congress-data/silver/congress_data/congress/bill_chunks/<bill_id>/data.jsonl
  open-leaks/silver/open_leaks/leaks/leak_chunks/data.jsonl    (unpartitioned)
```

These are the same paths `MinioIOManager` writes in production. `task dev` and
the integration tests use `LocalJsonIOManager` (its drop-in filesystem analog
in `dagster_io.local_io_manager`); prod uses the MinIO variant. Backend
selection happens in each code location's `Definitions` via
`select_io_managers(default_local_dir=...)`, which reads `DAGSTER_IO_BACKEND`
(`local` | `minio`) from the env.

**Cached audio artifacts** (slow stages only, gitignored, regenerable):
```
.test-output/media-ingest/
    pipeline-cache/                  # expensive audio-model outputs (slow stages only)
        <doc_id>/                    # one subdir per video in audio_manifest.yaml
            0_transcription.json     # Whisper output for this video
            1_diarization.json       # pyannote output for this video
        # Flat pipeline-cache/0_transcription.json + 1_diarization.json
        # remain readable as the single-video fallback for legacy
        # `task bench:pipeline:warm` runs (doc_id=None path).
    model_cache/                     # local model weights cache (gitignored)
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
subdir; pass `doc_id=None` to read/write the flat single-video paths.

**Cross-domain chunk loading.** `tests/shared/medallion.py::load_chunks()` is
the only path through which the harness reads chunks. It globs the medallion
tree across all 3 domains (gold + silver, partitioned + unpartitioned) and
returns a merged list of dicts. Filter by `doc_ids=[...]` to narrow to
specific documents. Pass `sample_per_domain=N` to cap rows per domain — open-leaks
materializes 3.6M+ chunks, so `sample_per_domain` is required for tractable
extraction in `test_extraction_e2e` and `task bench:run`. The legacy
`BenchmarkStore.load_chunks` / `load_benchmark_chunks` paths (and the
`tests/fixtures/<domain>/benchmark_chunks.json` fixtures) are gone — there is
exactly one source of truth for chunks now.

## Test Structure

Cross-domain concerns at the root; each domain owns its own pipeline tests
and chunks materialization:

```
tests/                              # cross-domain only
    conftest.py                     # shared Dagster fixtures, safe env defaults
    benchmark_harness.py            # CLI entry point (interactive + flags)
    benchmark_config.py             # model registry + ensemble model panels
    test_extraction_e2e.py          # cross-domain LLM extraction (consumes load_chunks())
    test_extraction_benchmark.py    # ground truth + model scoring
    shared/
        __init__.py
        medallion.py                # load_chunks() — globs the medallion tree across all domains
        store.py                    # BenchmarkStore + RunStore (harness-lifecycle artifacts only)
        ground_truth.py             # ensemble consensus logic
        report.py                   # report builder for viewer SPA
        extraction_scoring.py       # mention/proposition F1 scoring

packages/media-ingest/tests/
    fixtures/
        audio_manifest.yaml         # 7 source videos -> stable doc_ids
        demo_video.mp4              # smallest fixture; default for single-video integration tests
    integration/
        conftest.py                 # media test fixtures
        test_pipeline_integration.py  # transcription / diarization / segment_merge
        test_pipeline.py            # full GPU chain
        test_chunks_cpu.py          # pre-seed segment_merge → materialize media_chunks (CPU)

packages/congress-data/tests/
    fixtures/
        bill_manifest.yaml          # bill_ids the integration test materializes
    integration/
        conftest.py                 # congress test fixtures + CLI options
        test_pipeline.py            # full bill pipeline: bronze -> silver -> gold (incl. test_bill_chunks)

packages/open-leaks/tests/
    integration/
        conftest.py                 # open-leaks test fixtures
        test_chunks.py              # leak_documents -> leak_chunks (no creds)
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

The `packages/media-ingest/tests/fixtures/audio_manifest.yaml` manifest maps a
list of source videos to stable `doc_id` slugs. Each entry drives a per-doc-id
branch of the pipeline cache so multiple videos can coexist without stomping
on each other.

```yaml
# packages/media-ingest/tests/fixtures/audio_manifest.yaml
videos:
  - doc_id: demo-video
    file: 'demo_video.mp4'
    title: 'demo_video'
  - doc_id: inside-the-aipac-pipeline
    file: 'Inside The AIPAC Pipeline.mp4'
    title: 'Inside The AIPAC Pipeline'
  # ...
```

Three steps wire the manifest from raw video to extraction artifacts. They run
in order; later steps depend on artifacts produced by earlier ones:

| Step | Wraps | Reads | Writes | When to run |
|------|-------|-------|--------|-------------|
| `scripts/fixtures/compress_fixtures.py` | `media_ingest.assets.transcode._transcode_video` | `*.mp4` source files | replaces `*.mp4` in place | After dropping new source videos into the fixture dir; brings them under git-friendly size |
| `scripts/fixtures/regen_audio_fixtures.py` (via `task bench:fixtures:regen`) | `_select_backend(MediaIngestConfig)` + `_run_diarization` | `*.mp4` + manifest | `pipeline-cache/<doc_id>/0_transcription.json`, `1_diarization.json` | After compressing fixtures, or when changing `WHISPER_BACKEND` / `MLX_MODEL_ID` |
| `task bench:chunks:regen:media` | `media_chunks` Dagster asset via `LocalJsonIOManager` | cached diarization (per-doc-id) | `.test-output/media-ingest/gold/media_ingest/media/media_chunks/<doc_id>/data.jsonl` | After audio regen, or when changing chunker config (`CHUNK_SIZE`, pause threshold) |
| `scripts/benchmark/bench_extract_per_video.py` | `extract_validated()` | per-doc-id chunks (via `load_chunks(doc_ids=...)`) | `runs/<run-id>/extractions/<doc_id>/extraction_<model>.json` (+ flat aggregate roll-up) | Invoked as a subprocess by `benchmark_harness.py --all-videos`; not run directly |

Taskfile shortcuts:

```bash
HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen
HF_TOKEN=hf_xxx task bench:fixtures:regen -- --only demo-video,inside-the-aipac-pipeline
HF_TOKEN=hf_xxx task bench:fixtures:regen -- --force  # bypass per-video cache check

task bench:chunks:regen                              # all 3 domains
task bench:chunks:regen:media                        # media_chunks (CPU-only, all videos)
task bench:chunks:regen:media -- -k demo-video       # narrow via pytest -k
task bench:chunks:regen:congress                     # bill_chunks (needs CONGRESS_API_KEY)
task bench:chunks:regen:leaks                        # leak_chunks (no creds)
```

Both pre-extraction steps are idempotent — `regen_audio_fixtures.py` skips a
`doc_id` whose two cache files already exist unless `--force` is set, and
`bench:chunks:regen:media` always recomputes from cached diarization since
the chunker is fast.

#### Multi-video benchmark

```bash
PYTHONPATH=. python tests/benchmark_harness.py --all-videos --models gliner-medium
```

`--all-videos` swaps the harness's per-model subprocess from the single-video
pytest path to `scripts/benchmark/bench_extract_per_video.py`, which iterates the
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
        -> .test-output/congress-data/silver/congress_data/congress/bill_chunks/<bill_id>/data.jsonl
    -> Gold: bill_mentions, bill_assertions, bill_embeddings
```

> Run `task install:amr` once (downloads ~500MB amrlib STOG checkpoint) before
> `task seed:congress --with-gold` — without the weights the AMR projection
> path silently emits zero `bill_assertions`.

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
| `TEST_OUTPUT_ROOT` | Override test output location | `.test-output` |
| `BENCH_ONLY_DOC_IDS` | Restrict `--all-videos` extraction to a subset (comma-separated `doc_id`s); set by harness when narrowing | -- |
| `BENCH_SAMPLE_PER_DOMAIN` | Cap chunks per domain in `test_extraction_e2e` and the harness's in-process fixtures. open-leaks materializes 3.6M+ chunks so a full extraction is intractable; set to `0` to disable the cap | `50` |
| `DAGSTER_S3_ENDPOINT_URL` | MinIO endpoint — `http://localhost:9000` for the Tilt-managed local container in dev, the cluster Tenant via Tiltfile.prod's port-forward in ops mode | `http://localhost:9000` |
| `WHISPER_MODEL_CACHE` | Local cache dir for Whisper model weights (env-driven; was previously hard-coded to `/data/whisper-models`). Local dev typically uses `~/.cache/whisper-models` | `~/.cache/whisper-models` |
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
- Add a new benchmark video by dropping the `.mp4` into `packages/media-ingest/tests/fixtures/`, appending an entry to `audio_manifest.yaml`, then running `task bench:fixtures:regen` followed by `task bench:chunks:regen:media`
