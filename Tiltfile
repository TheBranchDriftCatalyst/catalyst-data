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

# DuckDB is in-process — no daemon. This resource is just a hub of
# inspect buttons for the latest bench run's parquet shards (CD-jzkg).
# Every button below shares the same path-resolution preamble: pick
# events.parquet if consolidated, else glob the per-process shards.
local_resource(
    'duckdb-inspect',
    cmd='echo "Click any button to interrogate the latest bench run\'s DuckDB audit log."',
    auto_init=False,
    trigger_mode=TRIGGER_MODE_MANUAL,
    labels=[LABEL_BENCH],
)

# Shared preamble: define $TARGET (the parquet path or shard glob) +
# ensure the duckdb CLI is installed. Keeps each button focused on its
# query. NOTE: this is the *Starlark* string; it gets injected verbatim
# into each button's shell command via Python f-string-style format.
_DUCKDB_PREAMBLE = '''
ROOT="${TEST_OUTPUT_ROOT:-./.test-output}/media-ingest/bench-cache"
if [ ! -f "$ROOT/events.parquet" ] && [ -z "$(ls "$ROOT"/events-*.parquet 2>/dev/null)" ]; then
  echo "No events parquet found under $ROOT. Run a bench first (bench:run)."
  exit 1
fi
if ! command -v duckdb >/dev/null 2>&1; then
  echo "duckdb CLI not installed. Install with:  brew install duckdb"
  exit 1
fi
if [ -f "$ROOT/events.parquet" ]; then
  TARGET="$ROOT/events.parquet"
else
  TARGET="$ROOT/events-*.parquet"
fi
'''

cmd_button(
    name='btn-duckdb-open',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
echo "Opening DuckDB on: $TARGET"
echo "Try:  SELECT node_name, status, count(*) FROM events GROUP BY 1,2 ORDER BY 3 DESC;"
duckdb -cmd "CREATE VIEW events AS SELECT * FROM read_parquet('$TARGET', union_by_name=true);"
'''],
    text='Open DuckDB shell',
    icon_name='terminal',
)

cmd_button(
    name='btn-duckdb-stats',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
echo "── node_name × status histogram ──"
duckdb -box -cmd "SELECT node_name, status, count(*) AS n FROM read_parquet('$TARGET', union_by_name=true) GROUP BY 1,2 ORDER BY n DESC LIMIT 50;"
echo
echo "── total events / per-source ──"
duckdb -box -cmd "SELECT source, count(*) AS n FROM read_parquet('$TARGET', union_by_name=true) GROUP BY 1 ORDER BY n DESC;"
echo
echo "── per-model event counts ──"
duckdb -box -cmd "SELECT model, count(*) AS n FROM read_parquet('$TARGET', union_by_name=true) WHERE model IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 20;"
'''],
    text='Stats (histograms)',
    icon_name='analytics',
)

cmd_button(
    name='btn-duckdb-errors',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
echo "── recent failed/error events (last 30) ──"
duckdb -box -cmd "
  SELECT
    strftime(ts, '%H:%M:%S.%f') AS time,
    source, node_name, status,
    coalesce(model, '-')   AS model,
    coalesce(doc_id, '-')  AS doc_id,
    coalesce(chunk_id, '-') AS chunk_id,
    json_extract_string(details, '$.error') AS error_msg
  FROM read_parquet('$TARGET', union_by_name=true)
  WHERE status IN ('error', 'failed', 'failure', 'timeout')
  ORDER BY ts DESC
  LIMIT 30;
"
'''],
    text='Errors (recent failures)',
    icon_name='error_outline',
)

cmd_button(
    name='btn-duckdb-schema',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
echo "── parquet schema (DESCRIBE) ──"
duckdb -box -cmd "DESCRIBE SELECT * FROM read_parquet('$TARGET', union_by_name=true);"
echo
echo "── shard files on disk ──"
ls -lh "${TEST_OUTPUT_ROOT:-./.test-output}/media-ingest/bench-cache"/events*.parquet 2>&1
echo
echo "── row counts per shard ──"
duckdb -box -cmd "SELECT filename, count(*) AS rows FROM read_parquet('$TARGET', union_by_name=true, filename=true) GROUP BY filename ORDER BY filename;"
'''],
    text='Schema + shard layout',
    icon_name='schema',
)

cmd_button(
    name='btn-duckdb-tail',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
echo "── last 20 events ──"
duckdb -box -cmd "
  SELECT
    strftime(ts, '%H:%M:%S.%f') AS time,
    source, node_name, status,
    coalesce(model, '-') AS model,
    coalesce(chunk_id, '-') AS chunk_id
  FROM read_parquet('$TARGET', union_by_name=true)
  ORDER BY ts DESC
  LIMIT 20;
"
'''],
    text='Tail (last 20)',
    icon_name='vertical_align_bottom',
)

cmd_button(
    name='btn-duckdb-runs-s3',
    resource='duckdb-inspect',
    argv=['sh', '-c', '''
if ! command -v duckdb >/dev/null 2>&1; then
  echo "duckdb CLI not installed. Install with:  brew install duckdb"
  exit 1
fi
ENDPOINT="${DAGSTER_S3_ENDPOINT_URL:-http://localhost:9000}"
ACCESS="${DAGSTER_S3_ACCESS_KEY:-minio}"
SECRET="${DAGSTER_S3_SECRET_KEY:-minio123}"
BUCKET="${DAGSTER_S3_BUCKET:-dagster}"
echo "── archived bench runs in s3://$BUCKET/bench/runs/ (events.parquet only) ──"
duckdb -box -cmd "
  INSTALL httpfs;
  LOAD httpfs;
  SET s3_endpoint = '${ENDPOINT#http://}';
  SET s3_url_style = 'path';
  SET s3_use_ssl = false;
  SET s3_access_key_id = '$ACCESS';
  SET s3_secret_access_key = '$SECRET';
  SELECT regexp_extract(file, 'runs/([^/]+)/', 1) AS run_id, count(*) AS rows
  FROM read_parquet('s3://$BUCKET/bench/runs/*/events.parquet', filename='file', union_by_name=true)
  GROUP BY 1 ORDER BY 1 DESC;
"
'''],
    text='List archived runs (S3)',
    icon_name='cloud',
)

# ── UI shortcuts: open key viewer surfaces in a browser ────────────────

cmd_button(
    name='btn-open-audit-viewer',
    resource='duckdb-inspect',
    argv=['sh', '-c', 'open http://localhost:5173/viewer/benchmarks'],
    text='Open Benchmark Report (UI)',
    icon_name='dashboard',
)

cmd_button(
    name='btn-open-state-inspector',
    resource='duckdb-inspect',
    argv=['sh', '-c', 'open http://localhost:5173/viewer/benchmarks/state'],
    text='Open State Inspector (UI)',
    icon_name='timeline',
)

cmd_button(
    name='btn-open-events-api',
    resource='duckdb-inspect',
    argv=['sh', '-c', '''
LATEST=$(curl -sm 3 http://localhost:8080/viewer/api/bench/runs 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('latest') or '')" 2>/dev/null)
if [ -z "$LATEST" ]; then
  echo "No bench runs found."
  exit 1
fi
echo "Opening /viewer/api/bench/runs/$LATEST/events?limit=50 in browser…"
open "http://localhost:8080/viewer/api/bench/runs/$LATEST/events?limit=50"
'''],
    text='Open /events API (latest run)',
    icon_name='api',
)

cmd_button(
    name='btn-open-diagnostics',
    resource='duckdb-inspect',
    argv=['sh', '-c', 'open http://localhost:8080/viewer/api/bench/diagnostics'],
    text='Open Diagnostics counter',
    icon_name='monitoring',
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
