"""HTTP routes for the BenchmarkRunner UI tab.

Mounted under ``/viewer/api/bench/runner``. Three concerns:

  * **Configs CRUD** — ``GET/POST/DELETE /configs[…]``. Stored as JSON
    files; see ``services/bench_runner.py`` for the storage shape.
  * **Run management** — ``POST /run`` to spawn the harness as a child
    process; ``GET /runs[/<id>][/log]`` to poll status + tail stdout;
    ``POST /runs/<id>/stop`` to SIGTERM.
  * **Models** — ``GET /models`` returns the registry from
    ``tests/benchmark_config.py`` so the form's dropdowns don't drift.

The runner runs in-process with the viewer-api FastAPI server. It
inherits the parent's env so ``LLM_API_KEY`` / ``DAGSTER_S3_*`` /
``CONGRESS_API_KEY`` flow through to the spawned harness without
re-loading ``.envrc.cluster``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from media_ingest.viewer.services import bench_runner as svc

router = APIRouter(prefix="/viewer/api/bench/runner", tags=["bench-runner"])


# ─── Models registry ────────────────────────────────────────────────────────


@router.get("/models")
def get_models() -> dict[str, Any]:
    """Return ``{tier_name: [{name, model, tags: list[str]}…]}`` for dropdowns.

    Response type left as ``dict[str, Any]`` because FastAPI's response
    validator coerces nested types and a strict ``dict[str, list[dict[str,
    str]]]`` rejects the ``tags: list[str]`` field on each model entry.
    """
    return svc.list_models()


# ─── Configs CRUD ───────────────────────────────────────────────────────────


@router.get("/configs")
def list_configs() -> list[dict[str, Any]]:
    return svc.list_configs()


@router.get("/configs/{config_id}")
def get_config(config_id: str) -> dict[str, Any]:
    cfg = svc.get_config(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"config {config_id!r} not found")
    return cfg


@router.post("/configs")
def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="config 'name' is required")
    return svc.save_config(payload)


@router.delete("/configs/{config_id}")
def delete_config(config_id: str) -> dict[str, bool]:
    ok = svc.delete_config(config_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"config {config_id!r} not found")
    return {"deleted": True}


# ─── Run lifecycle ──────────────────────────────────────────────────────────


@router.post("/run")
def start_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Spawn the harness with the given config inline.

    Payload shape mirrors the :class:`BenchConfig` dataclass; an optional
    ``sample_per_domain`` overrides the env var for this run only.
    """
    config = dict(payload)
    sample_n = config.pop("sample_per_domain_override", None)
    try:
        return svc.start_run(config, sample_per_domain=sample_n)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs")
def list_runs() -> list[dict[str, Any]]:
    return svc.list_runs()


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    r = svc.get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return r


@router.get("/runs/{run_id}/log")
def get_run_log(run_id: str, max_bytes: int = 200_000) -> dict[str, str]:
    """Return the run's combined stdout/stderr tail (≤ max_bytes)."""
    r = svc.get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return {"log": svc.tail_log(run_id, max_bytes=max_bytes)}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, bool]:
    ok = svc.stop_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return {"stopped": True}
