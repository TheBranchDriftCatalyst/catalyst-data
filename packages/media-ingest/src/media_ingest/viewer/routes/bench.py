"""Bench routes — surface S3-backed benchmark artifacts to the viewer SPA.

Replaces the prior shape where the bench harness wrote into Vite's
``publicDir`` (``.test-output/media-ingest/...``) and the React app fetched
``/viewer/...`` directly off disk. Now the harness writes to
``s3://<bucket>/bench/...`` and these routes proxy reads from S3 to the SPA.

Live tail (during a running bench): the bench harness keeps an
``events.jsonl`` on local disk under ``S3BenchmarkStore.local_cache_root``
and runs a WebSocket bus on a free port; ``GET /viewer/api/bench/bus-port``
exposes that port so the viewer can connect to the live stream. Once the
run completes, the events file is archived to S3 at
``s3://<bucket>/bench/runs/<run_id>/events.jsonl`` and replays read it
from there via :func:`stream_run_events`.

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
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from dagster_io.bench import S3BenchmarkStore
from dagster_io.logging import get_logger

logger = get_logger(__name__)

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


@router.get("/runs/{run_id}/events.jsonl")
def run_events(run_id: str) -> StreamingResponse:
    """Stream the archived events.jsonl for a completed run.

    Live runs serve their tail via the run-bus WebSocket — see
    :func:`bus_port`. Once the run completes the harness uploads the
    file to S3 and replays come from here.
    """
    store = _bench_store()
    run = store.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    text = run.load_events_text()
    if text is None:
        raise HTTPException(status_code=404, detail=f"no events for run: {run_id}")

    def _gen():
        # FastAPI streams in chunks so very large event logs don't materialize
        # in one buffer. JSONL is line-delimited so chunk boundaries are safe.
        chunk = 64 * 1024
        for i in range(0, len(text), chunk):
            yield text[i : i + chunk]

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


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


# ─────────────────────────────────────────────────────────────────────────────
# Live run discovery
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/bus-port")
def bus_port() -> dict[str, Any]:
    """Return the WebSocket port the run-bus is listening on, if a bench
    run is currently active. Reads ``<local_cache_root>/.bus-port`` —
    the harness writes that file at run start and overwrites the previous
    value on each new run."""
    store = _bench_store()
    f = store.local_cache_root / ".bus-port"
    if not f.exists():
        return {"port": None, "active": False}
    try:
        port = int(f.read_text().strip())
    except (ValueError, OSError):
        return {"port": None, "active": False}
    return {"port": port, "active": True}


@router.get("/events/live")
def events_live() -> StreamingResponse:
    """Server-Sent Events stream of the live ``events.jsonl`` while a run
    is in flight.

    Why this exists: Vite proxies ``/viewer/bus`` to the run-bus
    WebSocket port, but Vite reads ``.bus-port`` once at startup. If
    the bench starts *after* Vite booted, the proxy points at nowhere
    and the SPA's WS handshake fails. SSE rides over the regular
    ``/viewer/api/*`` proxy (which Vite has stably wired to viewer-api),
    so it's robust to Vite-time port resolution issues — handy for the
    StateInspector + AuditViewer mid-run.

    The endpoint emits each line of ``events.jsonl`` as a single SSE
    ``data:`` frame and tails the file (poll every 250ms) until the
    client disconnects.
    """
    import asyncio
    import json as _json

    store = _bench_store()
    events_path = store.local_cache_root / "events.jsonl"

    async def _stream():
        # Backfill: emit everything currently on disk first so the SPA
        # gets the full audit history for the active run, not just events
        # arriving after the SSE connect.
        offset = 0
        if events_path.exists():
            with events_path.open("rb") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        # Validate JSON before forwarding so a partial last
                        # line (writer flushed mid-line) doesn't poison the SSE.
                        _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    yield b"data: " + line.rstrip(b"\n") + b"\n\n"
                offset = f.tell()

        # Tail loop. Poll the file size; when it grows, read + emit the new bytes.
        # Cheap on local disk (250ms poll); the run-bus already does push, this
        # is the fallback for clients that can't reach the WS.
        while True:
            await asyncio.sleep(0.25)
            if not events_path.exists():
                continue
            try:
                size = events_path.stat().st_size
            except OSError:
                continue
            if size <= offset:
                continue
            with events_path.open("rb") as f:
                f.seek(offset)
                buf = f.read(size - offset)
                offset = f.tell()
            for line in buf.splitlines():
                if not line.strip():
                    continue
                try:
                    _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                yield b"data: " + line + b"\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable nginx-style proxy buffering so each event flushes
            # immediately rather than batching at the proxy layer.
            "X-Accel-Buffering": "no",
        },
    )


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
