# Multi-Video Benchmark Workflow: Manifest, Compression, and Per-Doc-Id Caching

*April 2026*

A walkthrough of the multi-video pipeline that the media-ingest benchmark harness now exercises. The original benchmark framework ([extraction-benchmark-framework](../extraction-benchmark-framework/README.md)) ran every model against one demo video. This post documents the path from "drop a folder of source videos in the fixtures dir" to "harness extracts across all of them with per-video artifacts."

## TL;DR

- Seven source videos (~9 hours of audio) compressed in place via Apple Silicon hardware HEVC: **6.9 GB → 824 MB at 8.55x overall** using `videotoolbox-hevc` on an M-series machine.
- Audio cache regenerated per-video using `mlx-whisper` (Metal) + `pyannote` (MPS): **14m31s** wall-clock for all 7 videos, 14 cache files (~33 MB total).
- Manifest-driven layout: `tests/fixtures/media-ingest/audio_manifest.yaml` maps source `.mp4` files to slugified `doc_id`s; everything downstream keys off that.
- Per-doc-id storage everywhere: `pipeline-cache/<doc_id>/{0_transcription,1_diarization}.json`, `extractions/<doc_id>/extraction_<model>.json`, `per_video_chunks/<doc_id>/benchmark_chunks.json`.
- Harness gains `--all-videos` flag — swaps the per-model subprocess from single-video pytest to `scripts/bench_extract_per_video.py` which iterates the manifest.
- **Not yet built**: per-(model, video) ensemble GT, per-pair F1 scoring, viewer SPA video selector. Tracked under beads **CD-vfiq**.

---

## The Manifest

Everything starts here:

```yaml
# tests/fixtures/media-ingest/audio_manifest.yaml
videos:
  - doc_id: demo-video
    file: 'demo_video.mp4'
    title: 'demo_video'
  - doc_id: inside-the-aipac-pipeline
    file: 'Inside The AIPAC Pipeline.mp4'
    title: 'Inside The AIPAC Pipeline'
  - doc_id: 4-9-26-full-show-video
    file: '4⧸9⧸26 Full Show Video.mp4'
    title: '4/9/26 Full Show Video'
  # ... 4 more
```

`doc_id` is what shows up as a subdir name everywhere downstream. The slug is generated from the title with characters that filesystems hate stripped out — but the `file` field keeps the original on-disk filename intact (slashes replaced with U+29F8 because macOS won't let you create a file literally named `4/9/26`).

Adding a new video is three steps:

1. Drop the `.mp4` into `tests/fixtures/media-ingest/`
2. Append an entry to `audio_manifest.yaml`
3. Run `task bench:fixtures:regen` then `task bench:chunks:regen`

That's it — no other config touchpoint.

---

## Stage 1: Compression (`scripts/compress_fixtures.py`)

Source videos straight off YouTube are typically 1080p H.264 or AV1 at 1-2 Mbps. For benchmark fixtures they're decorative — the audio is what matters, and even that gets resampled to mono 16 kHz inside Whisper. So the script aggressively shrinks them:

```bash
python scripts/compress_fixtures.py tests/fixtures/media-ingest/ \
  --scale 480 --vt-bitrate 200k --audio-mono-16k
```

Defaults are `videotoolbox-hevc` on macOS, `svt-av1` elsewhere, scale to 480p, Opus 64 kbps stereo audio. The `--vt-bitrate 200k --audio-mono-16k` flags above push the floor lower for fixture use.

### Three encoder backends

The script dispatches through `media_ingest.assets.transcode._transcode_video(input_path, backend, **kwargs)` — the production transcode dispatcher. Same code path as the Dagster `media_transcode` asset that runs on the homelab cluster, just driven from a CLI:

| Backend | Hardware | Best for | Speed | Output |
|---------|----------|----------|-------|--------|
| `qsv` | Intel Quick Sync (i915 GPU) | Production k8s cluster | Fast | AV1 (`av1_qsv`) |
| `videotoolbox-hevc` | Apple Silicon (M-series) | Mac dev / fixture compression | ~10-20x faster than svt-av1 | HEVC (`hvc1` tagged for QuickTime) |
| `svt-av1` | Software AV1 (libsvtav1) | CI / fallback / smallest output | Slowest | AV1 |

For fixtures, output codec doesn't matter — whatever ffmpeg can decode is fine. So the videotoolbox path is preferred on Mac dev machines because it's an order of magnitude faster, with output ~1.3-1.5x larger than svt-av1 at equivalent perceived quality.

### Result

Running the seven source videos through compression:

```
6.9 GB → 824 MB total (8.55x overall)
```

The largest source went from ~2 GB (1080p H.264 at 5+ Mbps) down to a manageable 200-300 MB. With this footprint the `tests/fixtures/media-ingest/` dir stays small enough to remain practical even though the actual `.mp4` files are gitignored — `git status` doesn't get noisy and `du` doesn't blow up the local checkout.

### Bitrate target vs quality mode

The script's `--vt-bitrate 200k` flag uses VideoToolbox's CBR-ish rate control with `-maxrate` + `-bufsize 1M` for predictable sizes. The alternative `--vt-quality 0-100` mode uses VT's internal quality scale, which is opaque and tends to overshoot expected sizes. For fixtures where you want a hard ceiling, bitrate mode wins; for one-offs where quality matters, quality mode is fine. Default is `--vt-quality 60`.

---

## Stage 2: Audio Cache Regen (`scripts/regen_audio_fixtures.py`)

Once the videos are compressed, run the audio pipeline against each one:

```bash
HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen
```

This calls `regen_audio_fixtures.py` which reads the manifest, then for each video:

1. **Transcription** — calls `media_ingest.assets.transcription._select_backend(MediaIngestConfig(...))` to get a backend (whisper/openvino/mlx-whisper) + a `transcribe_fn`. Same dispatcher the production `media_transcriptions` asset uses, env-driven config. The dispatcher returns `(model, resolved_device, model_label, transcribe_fn)` and the script calls `transcribe_fn(model, video_path)`.
2. **Fidelity validator** — runs `_validate_transcription_fidelity(result, backend, label)` which inspects the result for known degradation patterns (no segments, suspicious word timestamps, language-detection mismatch with backend defaults) and warns.
3. **Diarization** — calls `_run_diarization(video_path, hf_token, local_cache)` which loads pyannote with auto-device-selection (`cuda` → `mps` → `cpu`) and runs speaker turn detection. The same `_assign_speakers(transcription_segments, diarization)` then merges speaker labels onto the Whisper segments.
4. **Save** — writes both artifacts under `pipeline-cache/<doc_id>/`:
   - `0_transcription.json` — Whisper segments + word timestamps + backend metadata
   - `1_diarization.json` — same segments with `speaker` field added + `speaker_count` + `diarization_device`

Banners get printed to stdout as it goes (`flush=True`) so you can see the pipeline progress live, not as a single block at the end.

### Run on M-series with mlx-whisper-base + pyannote on MPS

End-to-end the seven videos took **14m31s** wall-clock. Per-video stats from the actual cache:

| `doc_id` | Audio duration | Segments | Speakers | Transcribe (mlx-base on Metal) | Diarize (pyannote on MPS) |
|----------|---------------:|---------:|---------:|-------------------------------:|--------------------------:|
| `demo-video` | 13m45s | 179 | 2 | 8.3s | 21.7s |
| `inside-the-aipac-pipeline` | 28m07s | 270 | 1 | 11.2s | 38.2s |
| `unmasking-benjamin-netanyahu-...` | 27m55s | 332 | 1 | 14.6s | 33.4s |
| `saagar-x-joe-kent-...` | 56m40s | 983 | 2 | 27.6s | 68.1s |
| `4-9-26-full-show-video` | 1h40m39s | 1281 | 12 | 44.4s | 133.2s |
| `sarah-paine-the-war-for-india-...` | 2h13m25s | 2738 | 2 | 55.7s | 155.4s |
| `joe-rogan-experience-2284-ian-carroll` | 2h40m55s | 2115 | 8 | 76.3s | 213.0s |
| **Totals** | **~8h41m** | **7898** | — | **~4m** | **~11m** |

A few things stand out:

- mlx-whisper on Metal is genuinely fast — the longest video (~2h41m) transcribed in 76 seconds. That's ~127x realtime.
- Diarization is the slow stage, ~3-4x the transcription time on the same hardware. Pyannote's segmentation model runs serially over audio chunks; speed scales with duration roughly linearly.
- Speaker counts are correct on inspection — `inside-the-aipac-pipeline` and `unmasking-benjamin-netanyahu` are single-narrator pieces; `4-9-26-full-show-video` is a multi-guest panel with 12 distinct voices.
- The full Show Video and JRE episodes account for >70% of the total audio but only ~50% of the wall-clock. There's headroom in the pipeline (largely diarization GPU utilization) on longer audio.

### Why same code path as production matters

The script doesn't reimplement transcription or diarization. It imports the production functions:

```python
from media_ingest.assets.transcription import _select_backend, _validate_transcription_fidelity
from media_ingest.assets.diarization import _run_diarization, _assign_speakers
```

This means the test fixture and the deployed Dagster asset on `mac-node` exercise the exact same code path. If a backend regression sneaks into `_select_backend`, the test catches it locally before the asset does in cluster.

---

## Stage 3: Chunk Fixture Regen (`scripts/regen_benchmark_chunks.py`)

Once audio cache exists, the chunker is fast:

```bash
task bench:chunks:regen
```

For each video:

1. Load `pipeline-cache/<doc_id>/1_diarization.json` (already has speaker labels).
2. Run `ChunkingResource().chunk_speaker_segments(diarization["segments"], document_id, title, metadata)` — same chunker the production `media_chunks` asset runs.
3. Save full chunks to `tests/fixtures/media-ingest/per_video_chunks/<doc_id>/benchmark_chunks.json`.
4. Append to a merged `tests/fixtures/media-ingest/benchmark_chunks.json` that `BenchmarkStore.load_benchmark_chunks()` consumes.

The chunker runs in milliseconds per video — it's a pure Python pass over already-segmented data. Re-run after changing chunker config (`CHUNK_SIZE`, pause threshold) or after audio regen.

### Curation: known weak spot

`regen_benchmark_chunks.py --max-chunks-per-video N` truncates each video's chunks to the first N. **The flag is still in the script but its use is discouraged for evaluation runs** — taking the first N chunks systematically biases the benchmark toward intros and cold-opens, which are atypical of the rest of the conversation in most podcast/lecture content. Models that handle intro greetings well will look disproportionately good.

A proper curation strategy is part of the deferred work tracked under:

- **CD-uu76** — embedding-derived chunking POC: log adjacent-chunk cosine similarity distributions on existing chunks, then layer semantic refinement on top of the speaker chunker behind a feature flag.
- **CD-6ef7** — the broader epic: per-domain chunking strategies via LangChain splitters, EmbeddingResource integration, per-subtype chunks assets.

Until that lands, the recommendation is to drop `--max-chunks-per-video` entirely and let the harness run wide. Disk and wall-clock are cheap relative to producing biased numbers.

---

## Stage 4: Multi-Video Extraction (`benchmark_harness.py --all-videos`)

The harness wires the multi-video flow through a single new flag:

```bash
PYTHONPATH=. python tests/benchmark_harness.py --all-videos --models gliner-medium
```

### How `--all-videos` rewires the harness

In `tests/benchmark_harness.py:48-112`, `_run_model(cfg, ..., all_videos=False)` toggles between two subprocess paths:

- `all_videos=False` (default) — runs `pytest tests/test_pipeline_integration.py -k extraction_produces_mentions` against `demo_video.mp4`. Single-video, legacy behavior.
- `all_videos=True` — runs `python scripts/bench_extract_per_video.py`. The script reads the manifest and iterates per-video.

Per-model env vars (`LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_STRUCTURED_METHOD`, `LLM_MAX_TOKENS`, `LLM_CONTEXT_WINDOW`) are set the same way for both paths — only the entrypoint differs.

### What `bench_extract_per_video.py` does per video

For each entry in the manifest:

1. Loads cached diarization at `pipeline-cache/<doc_id>/1_diarization.json`. If missing, logs a warning and skips that video — re-run `task bench:fixtures:regen` to populate.
2. Runs `ChunkingResource().chunk_speaker_segments(...)` to produce `TextChunk`s.
3. Calls `extract_validated(chunks, code_location="media_ingest", max_concurrency=1)` from `dagster_io.extraction` — same function the production `media_mentions` and `media_assertions` Dagster assets use.
4. Saves the extraction at `runs/<run-id>/extractions/<doc_id>/extraction_<model>.json` via `RunStore.save_extraction(model, data, doc_id=...)`.

Per-video lines stream back to the harness:

```
  [1/7] demo-video: 53 chunks → 47 mentions, 12 assertions in 4.3s
  [2/7] inside-the-aipac-pipeline: 88 chunks → 73 mentions, 19 assertions in 7.1s
  ...
```

### Aggregate roll-up

After iterating all videos, the script writes a flat aggregate roll-up to `runs/<run-id>/extractions/extraction_<model>.json`. This aggregate has:

- `model`, `all_videos: true`, `doc_ids[]`
- `mentions: []`, `assertions: []` — **empty by design**; the per-doc-id files are the source of truth and flattening would duplicate ~7x.
- `stats.per_video[]` — array of `{doc_id, chunks, mentions, assertions, duration_s}` per video.
- `stats.{video_count, chunk_count, mention_count, assertion_count, duration_s}` — totals.

The harness uses this aggregate file to print summary lines and to ensure the existing report-builder code (which expects a flat `extraction_<model>.json`) finds something. The viewer SPA today reads from this aggregate; per-video drill-down requires CD-vfiq to land first.

### Restricting to a subset

When iterating on one video, set `BENCH_ONLY_DOC_IDS=demo-video,inside-the-aipac-pipeline` in the env — the script honors a comma-separated allowlist. This is also what the harness sets internally when narrowing.

---

## What's not yet built

This is the honest section. The multi-video infrastructure produces extraction artifacts but stops short of producing per-video evaluation data:

**Per-(model, video) ensemble GT.** `_run_ensemble_gt` in the harness today builds a single GT from one set of per-model extractions. It needs to iterate manifest `doc_id`s, build a per-video GT, save with `doc_id` keying. Either as `ground-truth/<doc_id>/active.json` (mirroring `extractions/<doc_id>/`) or a single `ground-truth/active.json` keyed by `doc_id` internally. Estimated 2-3 hours per CD-vfiq phase B.

**Per-pair F1 scoring.** `_score_latest` and `compute_model_scores` operate on a single chunks+GT pair. Multi-video scoring needs to iterate per `(model, doc_id)`, compute per-pair F1, then aggregate. Open question: weight per-video F1 in the per-model rollup chunk-weighted vs equal-per-video; macro-F1 vs micro-F1. Default to micro for back-compat. Phase C.

**Report builder.** `build_report_json` produces a flat per-model report. Needs a `per_video` section (rows = models, cols = videos × {strict_f1, mention_count, ...}) plus the per-model rollup. Phase D.

**Viewer SPA.** Add a video selector tab/column. Phase E (frontend work).

**Curation strategy.** As covered above — `--max-chunks-per-video N` is a placeholder that biases toward video intros. Real curation needs either semantic sampling or domain-stratified manual review. Tracked under CD-uu76 / CD-6ef7.

What today's `--all-videos` run is good for in the meantime:

- Per-model wall-clock time across an actual workload distribution. Demo video is ~14 minutes; the longest manifest video is 2h41m. Some models that look fine on demo-video tank on real-length content.
- Sanity-checking extraction stability across content domains (panel discussion vs single-narrator lecture vs interview).
- Producing the data substrate that the CD-vfiq scoring layer will consume once built — running the extractions now means the GT/scoring work is pure additive layering, no rerun required.

---

## Practical commands cheat sheet

```bash
# One-time fixture prep (after dropping new videos into the fixture dir)
python scripts/compress_fixtures.py tests/fixtures/media-ingest/
HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen
task bench:chunks:regen

# Narrow regen to specific videos
HF_TOKEN=hf_xxx task bench:fixtures:regen -- --only demo-video,inside-the-aipac-pipeline
task bench:chunks:regen -- --only saagar-x-joe-kent-resignation-israeli-nukes-epstein-charlie-kirk-mike-huckabee

# Force regen even if cache hits
HF_TOKEN=hf_xxx task bench:fixtures:regen -- --force

# Skip diarization (HF_TOKEN unavailable)
WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen -- --skip-diarization

# Run multi-video extraction for one model
PYTHONPATH=. python tests/benchmark_harness.py --all-videos --models gliner-medium

# Run multi-video for all local models
PYTHONPATH=. python tests/benchmark_harness.py --all-videos --local-only

# Single-video flow remains unchanged
task bench
task bench:pipeline:warm
```

### Relevant file paths

- Manifest: `tests/fixtures/media-ingest/audio_manifest.yaml`
- Compression: `scripts/compress_fixtures.py`
- Audio regen: `scripts/regen_audio_fixtures.py`
- Chunk regen: `scripts/regen_benchmark_chunks.py`
- Extraction subprocess: `scripts/bench_extract_per_video.py`
- Harness flag: `tests/benchmark_harness.py:474-480` (`--all-videos`)
- Per-doc-id store APIs: `tests/shared/store.py:101-141` (RunStore), `tests/shared/store.py:298-336` (BenchmarkStore)
- Transcode dispatcher: `packages/media-ingest/src/media_ingest/assets/transcode.py:255-269` (`_transcode_video`)
- Transcription dispatcher: `packages/media-ingest/src/media_ingest/assets/transcription.py` (`_select_backend`)
- Chunker: `libs/dagster-io/src/dagster_io/chunking.py` (`ChunkingResource.chunk_speaker_segments`)

---

*See also: [extraction-benchmark-framework](../extraction-benchmark-framework/README.md) for the original 12-model benchmark framework, [BENCHMARK.md](../../../BENCHMARK.md) for the full benchmark reference, [TESTING.md](../../../TESTING.md) for the integration test layout.*
