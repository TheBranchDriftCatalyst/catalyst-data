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
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from dagster_io.bench import S3BenchmarkStore
from dagster_io.logging import get_logger

logger = get_logger(__name__)

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
    store = _bench_store()
    gt = store.load_ground_truth(name)
    if gt is None:
        raise HTTPException(status_code=404, detail=f"ground truth not found: {name}")
    return gt


@router.put("/ground-truth/{name}.json")
async def put_ground_truth(name: str, request: Request) -> dict[str, Any]:
    """Replaces Vite's gtSavePlugin. The viewer-ui's GroundTruthPanel PUTs
    the entire JSON document; we save it to ``bench/ground-truth/<name>.json``.
    """
    store = _bench_store()
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="ground truth body must be a JSON object")
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
