# Local development

This repo has two Tilt entry points. **Default is fully local** — no
cluster contact, no MinIO, no kubectl. The cluster ops dashboard is its
own file, opt-in.

## Quickstart — local dev (the common case)

```bash
tilt up                          # default — uses ./Tiltfile
```

Brings up:

- `dagster-dev` — all three code locations (`media_ingest`,
  `congress_data`, `open_leaks`) in one process via `task dev`, wired
  to `LocalJsonIOManager`. Outputs land in `.test-output/<domain>/...`
  with the same medallion paths as production. Dagster UI on
  <http://localhost:3000>.
- `viewer-api` — FastAPI backend (`task dev:viewer:api:local`) on
  <http://localhost:8080> reading from `.test-output/` via the
  `LocalFsBackend`. Exposes `/viewer/api/s3/*` and `/viewer/api/...`
  document endpoints just like the prod API.
- `viewer-ui` — Vite SPA (`task dev:viewer:ui`) on
  <http://localhost:5173/viewer/>. The S3 Explorer tab browses your
  on-disk medallion tree as if it were a real bucket.
- `bench:run` — manual trigger for the benchmark harness; writes runs
  under `.test-output/media-ingest/runs/<timestamp>/`.

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

## Backend selection

The viewer's data layer reads `DAGSTER_IO_BACKEND`:

- `local` (set by `task dev:viewer:api:local`) → `LocalFsBackend`
  rooted at `.test-output/media-ingest/` (override with
  `CATALYST_LOCAL_BASE_DIR`).
- anything else → real `S3Client` against `DAGSTER_S3_ENDPOINT_URL`
  + creds env vars.

Same env var that flips Dagster's IO managers — keeps the dagster code
servers and the viewer in lockstep on which backend is in play.

## Running just the harness, no Tilt

```bash
task bench:run                   # writes to .test-output/.../runs/
task dev:viewer:ui               # SPA only
task dev:viewer:api:local        # FastAPI against .test-output/
task dev:viewer:api              # FastAPI against cluster MinIO
                                 # (requires Tiltfile.prod's port-forward
                                 #  or a manual `kubectl port-forward
                                 #  svc/minio 9000:9000 -n minio`)
```

`task dev:viewer:` is a non-Tilt orchestrator that runs both UI + API
together — useful when you don't want a Tilt session running.

## What if my system-installed `python` doesn't have dagster?

The Taskfile invokes `mise exec -- python ...` so the project's mise
toolchain (which has dagster, fastapi, uvicorn, boto3 installed) wins
even if a `.venv/bin/python` is first in `$PATH`. If `mise` itself is
missing, install it from <https://mise.jdx.dev>.
