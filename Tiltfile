# Tiltfile — Catalyst Data dev mode (single S3 backend, local MinIO container).
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
#
# When this Tiltfile runs as the entry point (`tilt up` from this dir),
# labels stay as-is (e.g. '1-infrastructure'). When it's include()d from
# the workspace aggregator at ../, every label gets prefixed with the
# project name so resources read like 'catalyst-data.1-infrastructure'
# — keeps catalyst-llm + catalyst-data resources distinguishable in the
# unified Tilt UI.
# ============================================
PROJECT_NAME = 'catalyst-data'
_running_standalone = config.main_dir.rstrip('/').endswith('/' + PROJECT_NAME)
# Project root resolves correctly whether this Tiltfile is the entry
# point (config.main_dir IS the catalyst-data dir) or include()'d from
# the workspace aggregator (config.main_dir is the workspace dir, and
# this project lives at workspace/catalyst-data/). Use PROJECT_DIR
# anywhere we need to construct absolute paths under catalyst-data.
PROJECT_DIR = config.main_dir if _running_standalone else os.path.join(config.main_dir, PROJECT_NAME)


def _label(base):
    if _running_standalone:
        return base
    return PROJECT_NAME + '.' + base


LABEL_INFRA = _label('1-infrastructure')
LABEL_DAGSTER = _label('2-dagster-platform')
LABEL_BENCH = _label('3-bench-viewer')
LABEL_OPS = _label('4-ops')

# ============================================
# INFRASTRUCTURE — k3d cluster + local overlay (CD-48tr, May 2026)
#
# MinIO + Neo4j now run inside the shared catalyst-dev k3d cluster
# (namespace: catalyst-data), not docker-compose. The Tilt-managed
# bring-up applies the kustomize overlay at k8s/local/, which renders
# minio.yaml (a local-only Deployment), Neo4j from k8s/base/platform/,
# and the n10s-init Job. Port-forwards keep the legacy localhost:9000 /
# 7474 / 7687 endpoints stable so host-Python Dagster (still run via
# `task dev`) doesn't notice the swap.
#
# Persistence: PVCs on the k3d default `local-path` storage class
# survive `tilt down` / `tilt up` cycles. Wipe via `kubectl delete pvc`.
# The old docker-compose volumes at .test-output/minio-data/ +
# .test-output/neo4j-data/ are NO LONGER USED — safe to delete.
# ============================================

# Shared dev cluster (catalyst-dev k3d) — defines `k3d-cluster` resource
# + calls allow_k8s_contexts('k3d-catalyst-dev'). Tilt dedupes when this
# is include()'d from multiple project Tiltfiles in the aggregator.
include('../../infra/k3d/cluster.Tiltfile')

# Render the local overlay. --load-restrictor=LoadRestrictionsNone lets
# the overlay reference individual files in ../base/ (Neo4j + namespace)
# without duplicating them into k8s/local/. Tilt is dev-only so the
# default kustomize security restrictor isn't load-bearing here.
k8s_yaml(kustomize('k8s/local', flags=['--load-restrictor=LoadRestrictionsNone']))

k8s_resource(
    'minio',
    port_forwards=['9000:9000', '9001:9001'],
    labels=[LABEL_INFRA],
    resource_deps=['k3d-cluster'],
)
k8s_resource(
    'minio-init',
    labels=[LABEL_INFRA],
    resource_deps=['minio'],
)
k8s_resource(
    'neo4j',
    port_forwards=['7474:7474', '7687:7687'],
    labels=[LABEL_INFRA],
    resource_deps=['k3d-cluster'],
)
k8s_resource(
    'neo4j-n10s-init',
    labels=[LABEL_INFRA],
    resource_deps=['neo4j'],
)

cmd_button(
    name='btn-open-minio-console',
    resource='minio',
    argv=['sh', '-c', 'open http://localhost:9001 || echo "Open http://localhost:9001 (minio / minio123)"'],
    text='Open MinIO Console',
    icon_name='open_in_browser',
)

cmd_button(
    name='btn-open-neo4j-browser',
    resource='neo4j',
    argv=['sh', '-c', 'open http://localhost:7474 || echo "Open http://localhost:7474 (neo4j / neo4j-homelab)"'],
    text='Open Neo4j Browser',
    icon_name='open_in_browser',
)

# ============================================
# DAGSTER PLATFORM (local dev mode)
#
# `dagster dev` runs all 3 code locations in one process. The wrapper
# script at scripts/dev/dagster_dev.py reads the two k8s manifests in
# k8s/local/ at *process* start, merges them into env, provisions the
# CATALYST_DATA_ROOT mirror, then execs dagster. Doing it in the script
# (not via serve_env=dict here) is intentional: Tilt evaluates the
# Tiltfile once and freezes serve_env for the session, so YAML edits
# wouldn't reach the live process without bouncing tilt. The wrapper
# re-reads on every restart — `tilt trigger dagster-dev` is enough.
#
#   dagster-dev-config.yaml  — committed; non-secret runtime config
#   dagster-dev-secrets.yaml — gitignored; copy from .yaml.example
# ============================================

local_resource(
    'dagster-dev',
    serve_cmd='uv run -- python scripts/dev/dagster_dev.py',
    deps=[
        'packages/media-ingest/src',
        'packages/congress-data/src',
        'packages/open-leaks/src',
        'libs/dagster-io/src',
        'k8s/local/dagster-dev-config.yaml',
        'k8s/local/dagster-dev-secrets.yaml',
        'scripts/dev/dagster_dev.py',
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
# Run the duckdb CLI via the davidgasquez/duckdb community image. Mount
# the bench-cache root at /data so all queries reference /data/events*.
# Using --platform linux/amd64 so this works on M-series Macs (the image
# is amd64-only); Docker Desktop emulates via Rosetta — fine for ad-hoc
# inspection. Pass any extra docker flags via $DOCKER_FLAGS (e.g. -it for
# the interactive shell button).
_DUCKDB_PREAMBLE = '''
ROOT="${TEST_OUTPUT_ROOT:-./.test-output}/bench-cache"
# Phase 4 (CD-jzkg.1) writes events under events/doc_id=*/data.parquet
# (or shard-*.parquet while a run is in flight). Phase 1-3 wrote a flat
# events.parquet (consolidated) and events-*.parquet (per-process
# shards). The downstream queries use read_parquet($TARGET,
# union_by_name=true, hive_partitioning=true) — the hive flag is a
# no-op on the legacy globs so a single flag covers both eras.
if compgen -G "$ROOT/events/doc_id=*/data.parquet" > /dev/null; then
  TARGET="/data/events/**/data.parquet"
elif compgen -G "$ROOT/events/doc_id=*/shard-*.parquet" > /dev/null; then
  TARGET="/data/events/**/shard-*.parquet"
elif [ -f "$ROOT/events.parquet" ]; then
  TARGET="/data/events.parquet"
elif compgen -G "$ROOT/events-*.parquet" > /dev/null; then
  TARGET="/data/events-*.parquet"
else
  echo "No events parquet found under $ROOT. Run a bench first (bench:run)."
  exit 1
fi
duck() {
  docker run --rm $DOCKER_FLAGS \\
    --platform linux/amd64 \\
    -v "$ROOT:/data:ro" \\
    --entrypoint duckdb \\
    davidgasquez/duckdb:latest "$@"
}
'''

cmd_button(
    name='btn-duckdb-open',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
DOCKER_FLAGS="-it"
echo "Opening DuckDB on: $TARGET (via davidgasquez/duckdb)"
echo "Try:  SELECT node_name, status, count(*) FROM events GROUP BY 1,2 ORDER BY 3 DESC;"
duck -cmd "CREATE VIEW events AS SELECT * FROM read_parquet('$TARGET', union_by_name=true, hive_partitioning=true);"
'''],
    text='Open DuckDB shell',
    icon_name='terminal',
)

cmd_button(
    name='btn-duckdb-stats',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
echo "── node_name × status histogram ──"
duck -box -cmd "SELECT node_name, status, count(*) AS n FROM read_parquet('$TARGET', union_by_name=true, hive_partitioning=true) GROUP BY 1,2 ORDER BY n DESC LIMIT 50;"
echo
echo "── total events / per-source ──"
duck -box -cmd "SELECT source, count(*) AS n FROM read_parquet('$TARGET', union_by_name=true, hive_partitioning=true) GROUP BY 1 ORDER BY n DESC;"
echo
echo "── per-model event counts ──"
duck -box -cmd "SELECT model, count(*) AS n FROM read_parquet('$TARGET', union_by_name=true, hive_partitioning=true) WHERE model IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 20;"
'''],
    text='Stats (histograms)',
    icon_name='analytics',
)

cmd_button(
    name='btn-duckdb-errors',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
echo "── recent failed/error events (last 30) ──"
duck -box -cmd "
  SELECT
    strftime(ts, '%H:%M:%S.%f') AS time,
    source, node_name, status,
    coalesce(model, '-')   AS model,
    coalesce(doc_id, '-')  AS doc_id,
    coalesce(chunk_id, '-') AS chunk_id,
    json_extract_string(details, '$.error') AS error_msg
  FROM read_parquet('$TARGET', union_by_name=true, hive_partitioning=true)
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
duck -box -cmd "DESCRIBE SELECT * FROM read_parquet('$TARGET', union_by_name=true, hive_partitioning=true);"
echo
echo "── shard files on disk ──"
DISK_ROOT="${TEST_OUTPUT_ROOT:-./.test-output}/bench-cache"
if [ -d "$DISK_ROOT/events" ]; then
  find "$DISK_ROOT/events" -type f -name '*.parquet' -exec ls -lh {} +
fi
ls -lh "$DISK_ROOT"/events*.parquet 2>/dev/null || true
echo
echo "── row counts per shard ──"
duck -box -cmd "SELECT filename, count(*) AS rows FROM read_parquet('$TARGET', union_by_name=true, hive_partitioning=true, filename=true) GROUP BY filename ORDER BY filename;"
'''],
    text='Schema + shard layout',
    icon_name='schema',
)

cmd_button(
    name='btn-duckdb-tail',
    resource='duckdb-inspect',
    argv=['sh', '-c', _DUCKDB_PREAMBLE + '''
echo "── last 20 events ──"
duck -box -cmd "
  SELECT
    strftime(ts, '%H:%M:%S.%f') AS time,
    source, node_name, status,
    coalesce(model, '-') AS model,
    coalesce(chunk_id, '-') AS chunk_id
  FROM read_parquet('$TARGET', union_by_name=true, hive_partitioning=true)
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
# S3 button doesn't need a local parquet — it queries minio via httpfs.
# Endpoint defaults to host.docker.internal:9000 since the duckdb container
# can't reach the host's localhost directly on Mac/Windows.
ENDPOINT="${DAGSTER_S3_ENDPOINT_URL:-http://host.docker.internal:9000}"
HOST="${ENDPOINT#http://}"; HOST="${HOST#https://}"
ACCESS="${DAGSTER_S3_ACCESS_KEY:-minio}"
SECRET="${DAGSTER_S3_SECRET_KEY:-minio123}"
BUCKET="${DAGSTER_S3_BUCKET:-dagster}"
echo "── archived bench runs in s3://$BUCKET/bench/runs/ (Phase 4 partitioned + legacy) ──"
docker run --rm --platform linux/amd64 \\
  -e S3_HOST="$HOST" -e S3_ACCESS="$ACCESS" -e S3_SECRET="$SECRET" -e S3_BUCKET="$BUCKET" \\
  --entrypoint duckdb davidgasquez/duckdb:latest -box -cmd "
    INSTALL httpfs; LOAD httpfs;
    SET s3_endpoint = getenv('S3_HOST');
    SET s3_url_style = 'path';
    SET s3_use_ssl = false;
    SET s3_access_key_id = getenv('S3_ACCESS');
    SET s3_secret_access_key = getenv('S3_SECRET');
    SELECT regexp_extract(file, 'runs/([^/]+)/', 1) AS run_id, count(*) AS rows
    FROM read_parquet([
      's3://' || getenv('S3_BUCKET') || '/bench/runs/*/events/doc_id=*/data.parquet',
      's3://' || getenv('S3_BUCKET') || '/bench/runs/*/events.parquet'
    ], filename='file', union_by_name=true, hive_partitioning=true)
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

cmd_button(
    name='btn-flush-diagnostics',
    resource='duckdb-inspect',
    argv=['sh', '-c', '''
echo "── before flush ──"
curl -sm 3 http://localhost:8080/viewer/api/bench/diagnostics | python3 -m json.tool
echo
echo "── flushing ──"
curl -sm 3 -X POST http://localhost:8080/viewer/api/bench/diagnostics/flush | python3 -m json.tool
'''],
    text='Flush diagnostics counter',
    icon_name='restart_alt',
)

cmd_button(
    name='btn-flush-duckdb',
    resource='duckdb-inspect',
    argv=['sh', '-c', '''
ROOT="${TEST_OUTPUT_ROOT:-./.test-output}/bench-cache"
echo "── files before flush in $ROOT ──"
ls -lh "$ROOT"/events*.parquet "$ROOT"/events.jsonl 2>/dev/null || echo "(no flat artefacts)"
if [ -d "$ROOT/events" ]; then
  find "$ROOT/events" -type f -name '*.parquet' -exec ls -lh {} + 2>/dev/null || echo "(empty events/ dir)"
else
  echo "(no events/ dir)"
fi
echo
echo "── deleting events/ tree, events.parquet, events-*.parquet, events.jsonl ──"
rm -rfv "$ROOT"/events 2>/dev/null
rm -fv "$ROOT"/events.parquet "$ROOT"/events-*.parquet "$ROOT"/events.jsonl 2>/dev/null
echo
echo "── files after ──"
ls -lh "$ROOT"/events*.parquet "$ROOT"/events.jsonl 2>/dev/null || echo "(empty — clean slate)"
[ -d "$ROOT/events" ] && echo "(events/ dir still present — should be gone)" || echo "(events/ dir gone)"
echo
echo "Diagnostics counter was NOT touched. Click \\"Flush diagnostics counter\\" to also reset that."
'''],
    text='Flush DuckDB (delete local parquet)',
    icon_name='delete_sweep',
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
