# scripts/

Operational tooling, grouped by purpose. Most files are invokable directly
(`python scripts/<sub>/<name>.py`); a few are wired into Taskfile.yml and
called via `task <name>`.

## Layout

| Subdir | Purpose |
|--------|---------|
| `training/`  | Pull/consume SFT + DPO JSONL for off-cluster fine-tuning |
| `benchmark/` | Extraction-benchmark helpers — invoked by `tests/benchmark_harness.py` |
| `fixtures/`  | Test-fixture media prep (compress, audio cache regen, chunk stats) |
| `ops/`       | Cluster + Dagster ops (k8s secrets, GraphQL materialize, audit dumps) |
| `dev/`       | Local-dev seeders/utilities (e.g. populate MinIO with sample data) |
| `analysis/`  | Repo-stats notebook and its output dir |
| `lib/`       | Shared shell helpers — symlinks into `../../talos-homelab/scripts/lib/` |

## File map

### `training/`
- `pull_training_dataset.py` — copy SFT/DPO JSONL from S3 to a local path
- `unsloth_finetune.py` — Unsloth + TRL fine-tune wrapper (GPU; opt-in via `unsloth` extra)

### `benchmark/`
- `bench_extract_per_video.py` — per-video extraction subprocess (run by `benchmark_harness.py --all-videos`)
- `sample_gt_candidates.py` — extraction-aware diversity sampler → `task bench:gt-candidates`

### `fixtures/`
- `compress_fixtures.py` — videotoolbox-hevc / svt-av1 in-place compression
- `regen_audio_fixtures.py` — regenerate transcription + diarization caches → `task bench:fixtures:regen`
- `chunk_stats.py` — analyze diarization/merge/chunking quality

### `ops/`
- `dashboard.sh` — k8s status display (sources `../lib/dashboard-common.sh`)
- `materialize.sh` — trigger a Dagster asset via GraphQL
- `qa-test.sh` — Dagster materialize/status/wait/logs wrapper
- `pull-dev-secrets.sh` — extract k8s secrets into per-domain `.envrc` files
- `dump_concordance.py` — Postgres concordance audit → CSV

### `dev/`
- `seed_local.py` — populate MinIO with a representative slice per domain via `dagster.materialize()`

### `analysis/`
- `repo_stats.ipynb` — AI-bloat / churn / coverage notebook → `task stats`
- `repo_stats_out/` — generated reports (gitignored output)

## Taskfile entry points

| Task | Script |
|------|--------|
| `task bench:gt-candidates` | `scripts/benchmark/sample_gt_candidates.py` |
| `task bench:fixtures:regen` | `scripts/fixtures/regen_audio_fixtures.py` |
| `task stats` / `task stats:run` | `scripts/analysis/repo_stats.ipynb` |
