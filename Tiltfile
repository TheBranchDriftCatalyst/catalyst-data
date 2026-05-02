# Tiltfile — Catalyst Data dev mode (single S3 backend, local MinIO container)
#
# Usage:
#   tilt up                    # local dev (this file — default)
#   tilt up -f Tiltfile.prod   # cluster observe-and-port-forward
#
# Dev architecture (same shape as prod, just local):
#   - MinIO runs as a local docker container with a persistent volume
#     at .test-output/minio-data/ (mc auto-creates the `dagster` bucket).
#   - Dagster code locations + viewer-api + bench harness all hit
#     http://localhost:9000 — identical S3 surface to prod, no
#     LocalJsonIOManager / LocalFsBackend / dual-mode complexity.
#   - Tilt manages the MinIO container + a one-shot bucket-init job
#     so `tilt up` is the only command needed.

# Load extensions
load('ext://uibutton', 'cmd_button', 'location')

print("""
======================================================================
  Catalyst Data — Local Dev Mode
======================================================================
  Storage : Local MinIO container (.test-output/minio-data/)
            S3 API: http://localhost:9000
            Console: http://localhost:9001  (minio / minio123)
  Backend : MinIO (same as prod, just local)

  For cluster ops dashboard:
      tilt up -f Tiltfile.prod
======================================================================
""")

# ============================================
# LABEL CONSTANTS
# ============================================
LABEL_INFRA = '1-infrastructure'
LABEL_DAGSTER = '2-dagster-platform'
LABEL_BENCH = '3-bench-viewer'
LABEL_OPS = '4-ops'

# ============================================
# INFRASTRUCTURE — Local MinIO via docker-compose
#
# `tilt up` boots the MinIO container with a persistent volume so the
# `dagster` bucket (and everything in it) survives `tilt down` cycles.
# The minio-init service auto-creates the bucket on first boot — the
# Tilt resource for it surfaces success/failure in its own log lane.
# ============================================

docker_compose('docker-compose.dev.yml')

dc_resource('minio', labels=[LABEL_INFRA])
dc_resource('minio-init', labels=[LABEL_INFRA], resource_deps=['minio'])

cmd_button(
    name='btn-open-minio-console',
    resource='minio',
    argv=['sh', '-c', 'open http://localhost:9001 || echo "Open http://localhost:9001 (minio / minio123)"'],
    text='Open MinIO Console',
    icon_name='open_in_browser',
)

# ============================================
# DAGSTER PLATFORM (local dev mode)
#
# `task dev` runs all 3 code locations in one process via `dagster dev`.
# Wired to MinioIOManager via DAGSTER_S3_ENDPOINT_URL=localhost:9000 so
# the medallion paths are identical to prod — same bucket, same keys,
# just a different endpoint.
# ============================================

local_resource(
    'dagster-dev',
    serve_cmd='task dev',
    deps=[
        'packages/media-ingest/src',
        'packages/congress-data/src',
        'packages/open-leaks/src',
        'libs/dagster-io/src',
    ],
    resource_deps=['minio-init'],
    auto_init=True,
    labels=[LABEL_DAGSTER],
)

cmd_button(
    name='btn-open-dagster-dev',
    resource='dagster-dev',
    argv=['sh', '-c', 'open http://localhost:3000 || echo "Open http://localhost:3000"'],
    text='Open Dagster (local)',
    icon_name='open_in_browser',
)

# ============================================
# BENCH / VIEWER — Local SPA + FastAPI backend
# ============================================

local_resource(
    'viewer-api',
    serve_cmd='task dev:viewer:api',
    deps=['packages/media-ingest/src/media_ingest/viewer'],
    resource_deps=['minio-init'],
    auto_init=True,
    labels=[LABEL_BENCH],
)

local_resource(
    'viewer-ui',
    serve_cmd='task dev:viewer:ui',
    auto_init=True,
    labels=[LABEL_BENCH],
)

cmd_button(
    name='btn-open-viewer',
    resource='viewer-ui',
    argv=['sh', '-c', 'open http://localhost:5173/viewer/ || echo "Open http://localhost:5173/viewer/"'],
    text='Open Viewer',
    icon_name='open_in_browser',
)

local_resource(
    'bench:run',
    cmd='task bench:run',
    deps=['tests/benchmark_harness.py', 'tests/benchmark_config.py'],
    resource_deps=['minio-init'],
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=[LABEL_BENCH],
)

# ============================================
# OPS — Local diagnostics
# ============================================

local_resource(
    'lint',
    cmd='task lint',
    deps=['libs', 'packages', 'tests', 'scripts'],
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=[LABEL_OPS],
)

local_resource(
    'tests:unit',
    cmd='task test',
    deps=['libs', 'packages', 'tests'],
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=[LABEL_OPS],
)

# ============================================
# CONFIGURATION
# ============================================

update_settings(
    max_parallel_updates=3,
    suppress_unused_image_warnings=None,
)

print("""
Ready! UI Groups:
  1-infrastructure    - minio (S3 API :9000, console :9001), minio-init
  2-dagster-platform  - dagster-dev (Webserver :3000, all 3 code locations)
  3-bench-viewer      - viewer-api (FastAPI :8080), viewer-ui (Vite :5173),
                        bench:run (manual)
  4-ops               - lint, tests:unit (manual)

Single backend: MinIO at http://localhost:9000 (persistent volume at
.test-output/minio-data/). Same medallion paths as prod, same code
path — just a different endpoint.
""")
