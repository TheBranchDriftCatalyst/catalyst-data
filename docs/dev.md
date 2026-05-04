# Local development

This repo has two Tilt entry points. **Default is fully local** — no
cluster contact, no MinIO, no kubectl. The cluster ops dashboard is its
own file, opt-in.

## Quickstart — local dev (the common case)

```bash
tilt up                          # default — uses ./Tiltfile
```

Brings up:

- `minio` — local MinIO container (docker-compose) with a persistent
  volume at `.test-output/minio-data/`. S3 API on
  <http://localhost:9000>, web console on <http://localhost:9001>
  (`minio` / `minio123`). The `dagster` bucket is auto-seeded by the
  `minio-init` one-shot.
- `dagster-dev` — all three code locations (`media_ingest`,
  `congress_data`, `open_leaks`) in one process via `task dev`, pointed
  at the local MinIO via `DAGSTER_S3_ENDPOINT_URL=http://localhost:9000`.
  Same medallion paths and same `S3Client` code path as prod — no
  dual-backend flags. Dagster UI on <http://localhost:3000>.
- `viewer-api` — FastAPI backend (`task dev:viewer:api`) on
  <http://localhost:8080>, also pointed at `localhost:9000`. Exposes
  `/viewer/api/s3/*`, `/viewer/api/bench/*`, and document endpoints.
- `viewer-ui` — Vite SPA (`task dev:viewer:ui`) on
  <http://localhost:5173/viewer/>. The S3 Explorer tab browses the
  local MinIO bucket; Benchmark/StateInspector tabs read from
  `/viewer/api/bench/*`.
- `bench:run` — manual trigger for the benchmark harness; writes
  artifacts to `s3://dagster/bench/runs/<timestamp>/...` in the local
  MinIO bucket. The bench audit log lands in per-process Parquet shards
  under `.test-output/bench-cache/` (`events-<pid>-<uuid>.parquet`)
  during the run, then consolidates to `events.parquet` and uploads to
  `s3://dagster/bench/runs/<run_id>/events.parquet` at run end. The
  StateInspector reads it via DuckDB at
  `/viewer/api/bench/runs/<run_id>/events`.

Run the bench, click around the viewer, materialize a Dagster asset
end-to-end without ever leaving your laptop.

## Cluster ops — `tilt up -f Tiltfile.prod`

```bash
tilt up -f Tiltfile.prod
```

This is the cluster observability dashboard:

- `k8s_attach`s for dagster-webserver / daemon / postgres / 4 code
  locations / Neo4j / postgres-knowledge / MinIO (statefulset).
- Port-forwards: 3000 (Dagster UI), 5432 (dagster-postgres), 5433
  (postgres-knowledge), 7474/7687 (Neo4j browser+bolt), 9000/9001
  (MinIO S3 API + console), 4001-4004 (code-location gRPC).
- The local `viewer-api` and `viewer-ui` resources also run, but
  `viewer-api` is wired to `task dev:viewer:api` (S3 mode at
  `localhost:9000`) so the SPA browses the live cluster bucket.
- Diagnostic resources delegate to `task cluster:*` — run them
  standalone too: `task cluster:health`, `task cluster:status`,
  `task cluster:validate-manifests`.

ArgoCD owns all the workloads — `Tiltfile.prod` only observes.

## One backend, two MinIOs

Same code path everywhere — only the `DAGSTER_S3_ENDPOINT_URL` differs:

- **Dev** (`tilt up`) — local MinIO container at `localhost:9000`,
  bucket data persisted to `.test-output/minio-data/`.
- **Prod ops** (`tilt up -f Tiltfile.prod`) — cluster MinIO Tenant
  reached via the Tiltfile's `k8s_attach` port-forward, also surfaced
  on `localhost:9000`.

No `DAGSTER_IO_BACKEND` flag, no `LocalFsBackend`, no dual-mode shims.
Whatever the env var resolves to is the one bucket everything talks to.

## Running just the harness, no Tilt

```bash
task bench:run         # writes to s3://dagster/bench/runs/<timestamp>/
task dev:viewer:ui     # SPA only
task dev:viewer:api    # FastAPI against whichever MinIO the env points at
```

Both viewer commands work in either mode — the env vars come from
`task dev` (dev) or your shell (prod-ops port-forward).

## Fine-tuning (local) — Phase 3 consumer

The Phase 3 training assets emit JSONL to S3:

- `s3://dagster/bench/training/sft/<domain>/data.jsonl` (full SFT)
- `s3://dagster/bench/training/dpo/<domain>/data.jsonl` (DPO pairs)

Local v1 consumer is Unsloth + TRL (CD-sduv). Workflow:

```bash
# 1. Pull the latest dataset down to a GPU box
python scripts/training/pull_training_dataset.py --kind sft --output ./sft.jsonl

# 2. (One-time) install the opt-in fine-tuning deps
uv pip install -e '.[unsloth]'

# 3. Fine-tune
python scripts/training/unsloth_finetune.py --kind sft \
    --dataset ./sft.jsonl \
    --base-model unsloth/llama-3.2-1b-Instruct-bnb-4bit \
    --output ./adapters/sft-media

# 4. (Optional) ship the adapter back to S3
aws s3 cp --recursive ./adapters/sft-media \
    s3://dagster/bench/training/adapters/sft-media/
```

`--dry-run` formats and previews rows without loading the model — runs
on any laptop, no GPU needed. Cluster-side fine-tuning is a separate
workstream.

## What if my system-installed `python` doesn't have dagster?

The Taskfile invokes `mise exec -- python ...` so the project's mise
toolchain (which has dagster, fastapi, uvicorn, boto3 installed) wins
even if a `.venv/bin/python` is first in `$PATH`. If `mise` itself is
missing, install it from <https://mise.jdx.dev>.
