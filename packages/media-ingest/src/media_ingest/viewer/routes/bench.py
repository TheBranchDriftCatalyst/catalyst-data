"""Bench routes — surface S3-backed benchmark artifacts to the viewer SPA.

Replaces the prior shape where the bench harness wrote into Vite's
``publicDir`` (``.test-output/media-ingest/...``) and the React app fetched
``/viewer/...`` directly off disk. Now the harness writes to
``s3://<bucket>/bench/...`` and these routes proxy reads from S3 to the SPA.

Audit log (CD-jzkg): the harness writes per-process Parquet shards to
local disk via ``BenchEventStore`` during a run, then consolidates to
``events.parquet`` and uploads to S3 at run end. The viewer reads via
``GET /viewer/api/bench/runs/<run_id>/events`` (DuckDB ``read_parquet``
against the local shards while live, ``httpfs`` against the S3 archive
post-hoc). The legacy jsonl endpoint was removed in Phase 3.

Phase 0 (CD-9wno) — GT span translation
----------------------------------------
GT files in S3 use doc-anchored spans (``doc_char_start`` / ``doc_char_end``
on each mention).  The SPA editor works in chunk-relative coordinates.
To keep the editing experience unchanged:

- **translate-on-read**: ``GET /viewer/api/bench/ground-truth/<name>.json``
  translates doc-frame mention spans back to chunk-relative ``span_start`` /
  ``span_end`` using the chunk metadata loaded from S3.  Chunks for which
  ``chunk_char_offset`` is absent are passed through unchanged.

- **translate-on-save**: ``PUT /viewer/api/bench/ground-truth/<name>.json``
  converts the incoming chunk-relative spans (from the SPA) back to
  doc-frame before persisting to S3.  The ``chunk_id`` sent by the SPA is
  used to look up the chunk metadata.

If the silver-layer chunks are unavailable (e.g. no MinIO data) the routes
degrade gracefully: read returns the GT as-is, save stores the GT as-is
(the SPA's spans will be chunk-relative, which is fine if the chunker hasn't
changed since the GT was generated).
"""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from dagster_io.bench import S3BenchmarkStore
from dagster_io.logging import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics counter (CD-jzkg)
#
# Module-global counters that observe how often the viewer reads the parquet
# audit log via DuckDB. Post-Phase-3 the ``jsonl_fallback`` slot is retained
# as an always-zero observation channel — useful for confirming "no errors
# on duckdb reads" at a glance. The frontend no longer falls back, but the
# ``/diagnostics/fallback`` POST endpoint is kept so any future client-side
# error path can surface here without backend changes.
# ─────────────────────────────────────────────────────────────────────────────

_diag_lock = threading.Lock()
_diag_counters: dict[str, Any] = {
    "reads": {"duckdb": 0, "jsonl_fallback": 0},
    "last_fallback_reason": None,
    "last_fallback_run_id": None,
    "last_duckdb_run_id": None,
}


def _diag_inc_duckdb(run_id: str) -> None:
    with _diag_lock:
        _diag_counters["reads"]["duckdb"] += 1
        _diag_counters["last_duckdb_run_id"] = run_id


def _diag_inc_fallback(run_id: str | None, reason: str | None) -> None:
    with _diag_lock:
        _diag_counters["reads"]["jsonl_fallback"] += 1
        _diag_counters["last_fallback_reason"] = reason
        _diag_counters["last_fallback_run_id"] = run_id


# ---------------------------------------------------------------------------
# GT span translation helpers (bench route internal)
# ---------------------------------------------------------------------------


def _build_chunk_offset_index(gt_doc: dict, s3_data_service) -> dict[str, dict]:
    """Return a mapping of chunk_id → chunk metadata for all legacy_chunk_ids
    in the GT document.

    Loads the silver-layer chunks once per unique document_id.  Falls back to
    an empty dict if S3 is unavailable.

    The returned dict maps ``chunk_id -> {chunk_char_offset, chunk_text_len}``
    so callers can translate doc-frame spans back to chunk-relative.
    """
    # Collect doc_ids referenced in this GT file
    doc_ids: set[str] = set()
    for entry in gt_doc.get("chunks", []):
        doc_id = entry.get("doc_id") or ""
        if doc_id:
            doc_ids.add(doc_id)

    offset_index: dict[str, dict] = {}
    for doc_id in doc_ids:
        try:
            chunks = s3_data_service.load_chunks(doc_id)
        except Exception:
            chunks = []
        for chunk in chunks:
            cid = chunk.get("chunk_id") or ""
            if not cid:
                continue
            meta = chunk.get("metadata") or {}
            text = chunk.get("text") or ""
            offset_index[cid] = {
                "chunk_char_offset": meta.get("chunk_char_offset"),
                "chunk_text_len": len(text),
            }
    return offset_index


def _gt_doc_to_chunk_frame(gt_doc: dict, offset_index: dict[str, dict]) -> dict:
    """Return a copy of *gt_doc* with mention doc-frame spans translated to
    chunk-relative ``span_start`` / ``span_end`` for the SPA editor.

    Populates back-compat aliases ``chunk.chunk_id`` = ``chunk.legacy_chunk_id``
    and ``chunk.text`` = ``chunk.text_excerpt`` so older editor paths still work.
    """
    result = copy.deepcopy(gt_doc)
    for entry in result.get("chunks", []):
        # Set back-compat aliases
        legacy_cid = entry.get("legacy_chunk_id") or entry.get("chunk_id") or ""
        entry.setdefault("chunk_id", legacy_cid)
        entry.setdefault("text", entry.get("text_excerpt", ""))

        chunk_meta = offset_index.get(legacy_cid, {}) if legacy_cid else {}
        offset = chunk_meta.get("chunk_char_offset")
        chunk_text_len = chunk_meta.get("chunk_text_len")

        for mention in entry.get("mentions", []):
            doc_start = mention.get("doc_char_start")
            doc_end = mention.get("doc_char_end")
            if doc_start is None or doc_end is None or offset is None:
                # Pass through — no translation possible
                mention.setdefault("span_start", None)
                mention.setdefault("span_end", None)
                continue
            chunk_start = doc_start - offset
            chunk_end = doc_end - offset
            # Clamp to valid range
            if chunk_text_len is not None and (chunk_end <= 0 or chunk_start >= chunk_text_len):
                mention["span_start"] = None
                mention["span_end"] = None
                continue
            if chunk_start < 0 or chunk_end < chunk_start:
                mention["span_start"] = None
                mention["span_end"] = None
                continue
            mention["span_start"] = chunk_start
            mention["span_end"] = chunk_end
    return result


def _gt_doc_to_doc_frame(gt_doc: dict, offset_index: dict[str, dict]) -> dict:
    """Return a copy of *gt_doc* with mention chunk-relative spans translated to
    doc-frame ``doc_char_start`` / ``doc_char_end`` for persistence.

    Uses ``chunk_id`` (or ``legacy_chunk_id``) on each GT entry to look up the
    corresponding chunk offset from ``offset_index``.
    """
    result = copy.deepcopy(gt_doc)
    for entry in result.get("chunks", []):
        legacy_cid = entry.get("legacy_chunk_id") or entry.get("chunk_id") or ""
        chunk_meta = offset_index.get(legacy_cid, {}) if legacy_cid else {}
        offset = chunk_meta.get("chunk_char_offset")

        # Recompute entry-level doc_char_start/end if missing
        if entry.get("doc_char_start") is None and offset is not None:
            text_excerpt = entry.get("text_excerpt") or entry.get("text") or ""
            entry["doc_char_start"] = offset
            entry["doc_char_end"] = offset + len(text_excerpt)
        if not entry.get("doc_id"):
            entry["doc_id"] = legacy_cid.rsplit(":chunk-", 1)[0] if legacy_cid else ""

        for mention in entry.get("mentions", []):
            span_start = mention.get("span_start")
            span_end = mention.get("span_end")
            if span_start is None or span_end is None or offset is None:
                # Preserve existing doc_char fields if present; otherwise leave None
                continue
            mention["doc_char_start"] = offset + span_start
            mention["doc_char_end"] = offset + span_end
    return result


router = APIRouter(prefix="/viewer/api/bench", tags=["bench"])

_store: S3BenchmarkStore | None = None


def _bench_store() -> S3BenchmarkStore:
    """Lazy singleton — same backend as the viewer's S3 explorer routes,
    pointed at whichever MinIO is running (Tilt-managed local container in
    dev, cluster Tenant via Tiltfile.prod's port-forward)."""
    global _store
    if _store is None:
        _store = S3BenchmarkStore()
    return _store


# ─────────────────────────────────────────────────────────────────────────────
# Runs
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    """List all benchmark runs in S3, newest first."""
    store = _bench_store()
    runs = store.list_runs()
    # Reverse so newest is first — matches viewer expectations.
    return {
        "runs": list(reversed(runs)),
        "latest": runs[-1] if runs else None,
        "uri": store.runs_uri,
    }


@router.get("/runs/{run_id}/report.json")
def run_report(run_id: str) -> dict[str, Any]:
    """Return the benchmark-report.json for a specific run."""
    store = _bench_store()
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    report = run.load_report()
    if report is None:
        raise HTTPException(status_code=404, detail=f"no report for run: {run_id}")
    return report


# ─────────────────────────────────────────────────────────────────────────────
# DuckDB-backed events endpoint (CD-jzkg)
#
# Parameterised facets — NOT raw SQL across HTTP. Cross-run JOINs and arbitrary
# scans are footguns the viewer never needs; the StateInspector / AuditViewer
# filter on (model, doc_id, chunk_id, node_name, status, since-ts).
#
# Path resolution:
#   - Live runs (run_id matches the active in-flight run): read from local
#     shard glob ``<local_cache_root>/events-*.parquet`` so the viewer sees
#     in-flight events without waiting for the run-end consolidation.
#   - Archived runs: read via DuckDB ``httpfs`` against
#     ``s3://<bucket>/bench/runs/<run_id>/events.parquet``.
#
# ``Live vs archived'' is decided by probing the local shard set: if the
# harness has written ``events-*.parquet`` shards under ``local_cache_root``
# for this run_id, we read the local glob; otherwise we fetch the
# consolidated S3 archive.
# ─────────────────────────────────────────────────────────────────────────────


# Whitelist of filterable column names — every facet param maps to one of
# these. Keeps the WHERE clause builder a closed set (NO untrusted column
# names ever flow into SQL).
_FILTER_COLUMNS: tuple[str, ...] = (
    "model",
    "doc_id",
    "chunk_id",
    "node_name",
    "status",
)

_MAX_LIMIT = 50_000
_DEFAULT_LIMIT = 5_000


def _local_run_ids(store: S3BenchmarkStore) -> set[str]:
    """Return the set of distinct ``run_id`` values present in the local
    shard set.

    The harness does NOT clean prior parquet shards at run start, so the
    local cache can carry shards from the previous run alongside the
    active one until manual cleanup. Every shard is single-run by
    construction (one writer-process per run, per ``event_store.py``), so
    a single-row probe per file is enough.
    """
    shards = list(store.local_cache_root.glob("events-*.parquet"))
    if not shards:
        return set()
    try:
        import duckdb  # noqa: PLC0415

        conn = duckdb.connect(":memory:")
        try:
            run_ids: set[str] = set()
            for shard in shards:
                shard_lit = "'" + str(shard).replace("'", "''") + "'"
                row = conn.execute(f"SELECT run_id FROM read_parquet({shard_lit}) LIMIT 1").fetchone()
                if row and row[0]:
                    run_ids.add(row[0])
            return run_ids
        finally:
            conn.close()
    except Exception:
        return set()


def _is_live_run(store: S3BenchmarkStore, run_id: str) -> bool:
    """Return True iff ``run_id`` has at least one local parquet shard.

    "Live" really means "served from the local cache rather than the S3
    archive" — once a run consolidates and uploads, the canonical artefact
    is the S3 ``events.parquet``. During a run, and for the brief window
    between consolidation and archive PUT, the local shards are
    authoritative.

    The local shard set may legitimately contain the previous run's
    leftovers (the harness doesn't clean shards at run start). Stale-shard
    rows are filtered out by the ``WHERE run_id = ?`` clause in the live
    read path (see ``run_events_duckdb``).
    """
    # In-process fast path: viewer-api shares the harness process under
    # certain dev orchestrations. Saves a parquet probe per request.
    try:
        from dagster_io.bench import event_store as _event_store

        active = _event_store.current_run_id()
        if active is not None and active == run_id:
            return True
    except Exception:
        pass
    return run_id in _local_run_ids(store)


def _duckdb_connect_with_s3(client) -> Any:
    """Open an in-memory DuckDB and configure ``httpfs`` against MinIO.

    Reuses the env wiring from ``bench/store.py`` (DAGSTER_S3_*) so the
    viewer talks to the same MinIO the harness wrote to. ``url_style=path``
    is required for MinIO (no virtual-hosted addressing).
    """
    import duckdb  # noqa: PLC0415 — lazy import; duckdb is a dagster-io dep

    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs")
    conn.execute("LOAD httpfs")

    endpoint_url = os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000")
    access_key = os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio")
    secret_key = os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123")

    # Strip scheme — DuckDB's ``s3_endpoint`` wants host:port, not full URL.
    use_ssl = endpoint_url.startswith("https://")
    host = endpoint_url.split("://", 1)[-1].rstrip("/")

    conn.execute(f"SET s3_endpoint='{host}'")
    conn.execute(f"SET s3_access_key_id='{access_key}'")
    conn.execute(f"SET s3_secret_access_key='{secret_key}'")
    conn.execute("SET s3_url_style='path'")
    conn.execute(f"SET s3_use_ssl={'true' if use_ssl else 'false'}")
    conn.execute("SET s3_region='us-east-1'")
    # ``client`` reference reserved for future per-bucket auth wiring; we
    # currently use a single MinIO env triple so no client lookup is needed.
    _ = client
    return conn


def _build_where_clause(
    *,
    model: str | None,
    doc_id: str | None,
    chunk_id: str | None,
    node_name: str | None,
    status: str | None,
    since: str | None,
) -> tuple[str, list[Any]]:
    """Compile facet params to a parameterised WHERE clause.

    Each filter contributes one ``column = ?`` clause and one positional
    parameter. NO string concatenation of values — the only literals in the
    SQL are column names from the closed whitelist ``_FILTER_COLUMNS``.
    """
    clauses: list[str] = []
    params: list[Any] = []
    facets = {
        "model": model,
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "node_name": node_name,
        "status": status,
    }
    for col, val in facets.items():
        if val is None or val == "":
            continue
        if col not in _FILTER_COLUMNS:  # pragma: no cover — guarded by static dict
            raise HTTPException(status_code=400, detail=f"unknown filter column: {col}")
        clauses.append(f"{col} = ?")
        params.append(val)
    if since:
        clauses.append("ts >= CAST(? AS TIMESTAMPTZ)")
        params.append(since)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def _row_to_event_dict(row: tuple, cols: list[str]) -> dict[str, Any]:
    """Shape a parquet row to match the ``RunEvent`` TS interface.

    ``state`` and ``details`` are stored as JSON-string columns in parquet
    (see ``event_store.py`` schema notes); deserialise here so the viewer
    sees ``Record<string, unknown>`` and not raw strings.
    """
    rec = dict(zip(cols, row, strict=False))
    # ts is a python datetime — emit as ISO8601 string for JSON wire compat.
    ts = rec.get("ts")
    if ts is not None and not isinstance(ts, str):
        rec["ts"] = ts.isoformat()
    # state / details: JSON-encoded strings → dicts
    for k in ("state", "details"):
        v = rec.get(k)
        if isinstance(v, str):
            try:
                rec[k] = json.loads(v) if v else {}
            except json.JSONDecodeError:
                rec[k] = {}
        elif v is None:
            rec[k] = {}
    return rec


@router.get("/runs/{run_id}/events")
def run_events_duckdb(
    run_id: str,
    model: str | None = Query(default=None),
    doc_id: str | None = Query(default=None),
    chunk_id: str | None = Query(default=None),
    node_name: str | None = Query(default=None),
    status: str | None = Query(default=None),
    since: str | None = Query(default=None, description="ISO8601 timestamp lower bound"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    response_format: str = Query(default="jsonl", alias="format", pattern="^(json|jsonl)$"),
):
    """DuckDB-backed audit log read.

    The single viewer-facing audit-log endpoint as of CD-jzkg Phase 3
    (the legacy ``events.jsonl`` route is gone). Returns 404 with
    ``{"detail": "no parquet for run"}`` when neither the local shard
    glob nor the S3 ``events.parquet`` resolves — the SPA shows
    "no events for run" without retry.
    """
    store = _bench_store()
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    where_sql, params = _build_where_clause(
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        node_name=node_name,
        status=status,
        since=since,
    )

    order_sql = "ORDER BY seq ASC, writer_pid ASC" if order == "asc" else "ORDER BY seq DESC, writer_pid DESC"

    is_live = _is_live_run(store, run_id)

    # Resolve the parquet source. Local shards win for live runs (lower
    # latency, includes still-buffering data); S3 wins for archived runs.
    rows: list[tuple] = []
    cols: list[str] = []
    try:
        if is_live:
            shard_glob = str(store.local_cache_root / "events-*.parquet")
            shards = list(store.local_cache_root.glob("events-*.parquet"))
            if not shards:
                raise HTTPException(status_code=404, detail="no parquet for run")
            import duckdb  # noqa: PLC0415

            conn = duckdb.connect(":memory:")
            try:
                # read_parquet doesn't accept ``?`` parameters for the
                # path argument; embed the glob (a Path-derived string,
                # never user input) as a literal with quote escaping.
                glob_lit = "'" + shard_glob.replace("'", "''") + "'"
                # Always WHERE-filter by run_id even on the live path —
                # the local cache is not cleaned between runs, so shards
                # from a prior run can sit alongside the active set
                # (see ``_local_run_ids``). Without this clause, the
                # response would mix events across runs.
                live_clauses = ["run_id = ?"]
                live_params: list[Any] = [run_id]
                if where_sql:
                    # ``where_sql`` always starts with " WHERE " — strip
                    # that prefix and AND it onto the run_id guard.
                    live_clauses.append(where_sql.removeprefix(" WHERE "))
                    live_params.extend(params)
                live_where = " WHERE " + " AND ".join(live_clauses)
                sql = (
                    f"SELECT * FROM read_parquet({glob_lit}, union_by_name=true)"
                    f"{live_where} {order_sql} LIMIT {int(limit)}"
                )
                cur = conn.execute(sql, tuple(live_params))
                cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchall()
            finally:
                conn.close()
        else:
            # Archived run — read events.parquet from S3 via httpfs.
            s3_uri = f"s3://{store.bucket}/{run.events_parquet_key}"
            conn = _duckdb_connect_with_s3(store.client)
            try:
                uri_lit = "'" + s3_uri.replace("'", "''") + "'"
                # union_by_name=true keeps us schema-evolution-safe across
                # mixed-shard runs (see CD-jzkg §9.3).
                sql = (
                    f"SELECT * FROM read_parquet({uri_lit}, union_by_name=true)"
                    f"{where_sql} {order_sql} LIMIT {int(limit)}"
                )
                try:
                    cur = conn.execute(sql, tuple(params))
                except Exception as e:  # noqa: BLE001
                    # Most common: HTTP 404 from S3 because no parquet was
                    # archived for this run (consolidate failed). Surface
                    # as 404 so the SPA shows "no events for run".
                    msg = str(e)
                    if "404" in msg or "NoSuchKey" in msg or "not found" in msg.lower():
                        raise HTTPException(status_code=404, detail="no parquet for run") from e
                    raise
                cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchall()
            finally:
                conn.close()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("duckdb events read failed run_id=%s: %s", run_id, e)
        raise HTTPException(status_code=500, detail=f"duckdb read failed: {e}") from e

    _diag_inc_duckdb(run_id)

    events = [_row_to_event_dict(r, cols) for r in rows]

    if response_format == "json":
        return JSONResponse({"run_id": run_id, "live": is_live, "count": len(events), "events": events})

    # jsonl: stream so very large result sets don't all materialise in one
    # response body. Newline-delimited JSON; empty body if no rows.
    def _gen():
        for ev in events:
            yield json.dumps(ev, default=str) + "\n"

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


@router.get("/diagnostics")
def diagnostics() -> dict[str, Any]:
    """Snapshot the audit-log read counters.

    Post-Phase-3, ``reads.duckdb`` ticks on every viewer fetch and
    ``reads.jsonl_fallback`` is permanently zero (the fallback path was
    removed). The slot is retained as a free check that no client-side
    error path is masquerading as a fallback.
    """
    with _diag_lock:
        # Shallow copy the nested dict so the response isn't mutated by a
        # concurrent counter increment.
        return {
            "reads": dict(_diag_counters["reads"]),
            "last_fallback_reason": _diag_counters["last_fallback_reason"],
            "last_fallback_run_id": _diag_counters["last_fallback_run_id"],
            "last_duckdb_run_id": _diag_counters["last_duckdb_run_id"],
        }


@router.post("/diagnostics/fallback")
async def diagnostics_fallback(request: Request) -> dict[str, Any]:
    """Bump the jsonl-fallback counter.

    Retained post-Phase-3 as a server-side observation channel for any
    future client-side error path. The current ``useRunStream`` does not
    POST here (no fallback exists); a non-zero counter means something
    novel is calling this route. Body: ``{"run_id": "...", "reason": "..."}``
    (both optional).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    run_id = (body or {}).get("run_id")
    reason = (body or {}).get("reason")
    _diag_inc_fallback(run_id, reason)
    with _diag_lock:
        return {"reads": dict(_diag_counters["reads"])}


@router.post("/diagnostics/flush")
def diagnostics_flush() -> dict[str, Any]:
    """Reset the audit-log counters to zero.

    Used between bench runs when the operator wants a clean ``reads.duckdb``
    count. Returns the previous snapshot so callers can confirm what was
    cleared.
    """
    with _diag_lock:
        previous = {
            "reads": dict(_diag_counters["reads"]),
            "last_fallback_reason": _diag_counters["last_fallback_reason"],
            "last_fallback_run_id": _diag_counters["last_fallback_run_id"],
            "last_duckdb_run_id": _diag_counters["last_duckdb_run_id"],
        }
        _diag_counters["reads"]["duckdb"] = 0
        _diag_counters["reads"]["jsonl_fallback"] = 0
        _diag_counters["last_fallback_reason"] = None
        _diag_counters["last_fallback_run_id"] = None
        _diag_counters["last_duckdb_run_id"] = None
    return {"flushed": True, "previous": previous}


@router.get("/runs/{run_id}/config.json")
def run_config(run_id: str) -> dict[str, Any]:
    """Return the run-config.json for a specific run, if present."""
    store = _bench_store()
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    try:
        import json as _json

        raw = store.client.get_object(run.config_key)
        return _json.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"no config for run: {run_id}") from e


# ─────────────────────────────────────────────────────────────────────────────
# Top-level report
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/report.json")
def top_report() -> dict[str, Any]:
    """Return the top-level benchmark-report.json — the latest run's report,
    copied here at run end for the viewer's default load path."""
    store = _bench_store()
    report = store.load_top_level_report()
    if report is None:
        raise HTTPException(status_code=404, detail="no top-level benchmark report yet")
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Ground truth — read + write
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/ground-truth")
def list_ground_truths() -> dict[str, Any]:
    store = _bench_store()
    return {
        "names": store.list_ground_truths(),
        "uri": store.ground_truth_uri,
    }


@router.get("/ground-truth/{name}.json")
def get_ground_truth(name: str) -> dict[str, Any]:
    """Return a GT file, translating doc-frame mention spans to chunk-relative
    ``span_start`` / ``span_end`` so the SPA editor works in its native
    coordinate system (translate-on-read, Phase 0 CD-9wno).

    Falls back to returning the raw GT when chunk metadata is unavailable.
    """
    store = _bench_store()
    gt = store.load_ground_truth(name)
    if gt is None:
        raise HTTPException(status_code=404, detail=f"ground truth not found: {name}")

    # Only attempt translation when the GT is in the new doc-anchored format.
    # Detection: any chunk entry has a ``doc_id`` field and no ``chunk_id``
    # (legacy format uses ``chunk_id`` as primary key).
    first_chunk = (gt.get("chunks") or [None])[0]
    if first_chunk and "doc_id" in first_chunk:
        try:
            from media_ingest.viewer.services.s3_data import S3DataService

            svc = S3DataService()
            offset_index = _build_chunk_offset_index(gt, svc)
            gt = _gt_doc_to_chunk_frame(gt, offset_index)
        except Exception:
            logger.debug("translate-on-read failed (degraded); returning GT as-is", exc_info=True)

    return gt


@router.put("/ground-truth/{name}.json")
async def put_ground_truth(name: str, request: Request) -> dict[str, Any]:
    """Replaces Vite's gtSavePlugin. The viewer-ui's GroundTruthPanel PUTs
    the entire JSON document; we translate incoming chunk-relative spans to
    doc-frame before persisting to S3 (translate-on-save, Phase 0 CD-9wno).

    Falls back to storing the body as-is if chunk metadata is unavailable.
    """
    store = _bench_store()
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="ground truth body must be a JSON object")

    # Translate chunk-relative spans → doc-frame before persisting.
    # The SPA sends ``chunk_id`` (or ``legacy_chunk_id``) on each GT chunk entry
    # which we use to look up the chunk's ``chunk_char_offset`` from S3.
    try:
        from media_ingest.viewer.services.s3_data import S3DataService

        svc = S3DataService()
        offset_index = _build_chunk_offset_index(body, svc)
        body = _gt_doc_to_doc_frame(body, offset_index)
    except Exception:
        logger.debug("translate-on-save failed (degraded); storing GT as-is", exc_info=True)

    key = store.save_ground_truth(name, body)
    return {"saved": True, "key": key, "name": name}


@router.get("/extractions")
def list_top_extractions(
    run_id: str | None = Query(
        default=None, description="If set, list extractions for this run instead of the top-level cache."
    ),
) -> dict[str, Any]:
    """List extraction model names — top-level by default, or scoped to a
    specific run when ``run_id`` is provided."""
    store = _bench_store()
    if run_id:
        run = store.load_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return {"models": run.list_extractions(), "scope": f"run:{run_id}"}
    return {"models": store.list_extractions(), "scope": "top-level"}
