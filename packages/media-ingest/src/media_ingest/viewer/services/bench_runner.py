"""Bench-runner service — config CRUD + harness subprocess management.

Backs the BenchmarkRunner UI tab. Handles:

  * Saved configs: JSON files under ``.test-output/bench-configs/<id>.json``.
    A "config" is a snapshot of all the harness CLI flags an operator
    cares about (ensemble, spo_models, ner_quorum, all_videos, …).
    Operators can save tweaks they want to come back to without retyping.

  * Runs: subprocess wrapper around ``tests/benchmark_harness.py``. The
    UI fires ``POST /viewer/api/bench/runner/run`` with a config (saved or
    inline) and gets back a ``run_id`` it can poll for status. stdout/stderr
    are tail-able via ``GET /viewer/api/bench/runner/runs/<id>/log``.

  * Models: ``GET /viewer/api/bench/runner/models`` returns the registry
    from ``tests/benchmark_config.py`` so the form's encoder/spo dropdowns
    don't drift from the harness.

The runner is single-process — it spawns ``python tests/benchmark_harness.py``
as a child of the viewer-api FastAPI process. We cap concurrent runs at
1 because the harness itself is heavy (encoders + Ollama load); a queue is
simpler than wedging ourselves with parallel runs that thrash the GPU.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# Where saved configs and run logs live. ``.test-output`` is gitignored
# and shared with the existing fixture/cache tooling, so a single mount
# keeps everything together.
#
# Repo-root resolution: walk up from this file looking for the marker
# files that only exist at the workspace root (``Taskfile.yml`` +
# ``tests/benchmark_harness.py``). Hard-coding ``parents[N]`` is brittle
# because the file is nested 6 levels deep
# (``packages/media-ingest/src/media_ingest/viewer/services/bench_runner.py``)
# and silently broke the run subprocess by spawning from ``packages/``.
def _find_repo_root(start: Path) -> Path:
    for p in (start, *start.parents):
        if (p / "Taskfile.yml").is_file() and (p / "tests" / "benchmark_harness.py").is_file():
            return p
    raise RuntimeError(
        f"could not locate repo root from {start} — expected to find "
        "Taskfile.yml + tests/benchmark_harness.py walking up the tree"
    )


_REPO_ROOT = _find_repo_root(Path(__file__).resolve())
_CONFIG_DIR = _REPO_ROOT / ".test-output" / "bench-configs"
_RUN_LOG_DIR = _REPO_ROOT / ".test-output" / "bench-runner-logs"
_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
_RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class BenchConfig:
    """A saved benchmark configuration.

    Mirrors the CLI flags the harness accepts. ``id`` is auto-generated
    on first save. Empty/None fields collapse to harness defaults.
    """

    id: str
    name: str
    description: str = ""
    # Core panel
    ensemble: list[str] = field(default_factory=list)  # encoder names
    spo_models: list[str] = field(default_factory=list)  # llm/cloud names
    ner_quorum: str = ""  # predicate expression or bare integer
    # Phase + scope flags
    all_videos: bool = False
    full: bool = False
    ensemble_only: bool = False
    spo_only: bool = False
    no_consensus: bool = False
    regen: bool = False
    # Tuning
    sample_per_domain: int | None = None
    extra_args: list[str] = field(default_factory=list)  # raw passthrough
    # Env overrides — merged into the spawned harness env. Curated common
    # keys (LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME, EMBEDDING_PROVIDER,
    # EMBEDDING_MODEL, CONGRESS_API_KEY, …) plus any user-added keys. Empty
    # values are dropped so the harness falls back to the parent process env.
    env_overrides: dict[str, str] = field(default_factory=dict)
    # Bookkeeping
    created_at: float = 0.0
    updated_at: float = 0.0


def list_configs() -> list[dict[str, Any]]:
    """Return every saved config as a dict, newest first."""
    out: list[dict[str, Any]] = []
    for p in sorted(_CONFIG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:  # noqa: BLE001
            continue
    return out


def get_config(config_id: str) -> dict[str, Any] | None:
    p = _CONFIG_DIR / f"{config_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Create or update a config. ``id`` optional; assigned on create."""
    cfg_id = payload.get("id") or uuid.uuid4().hex[:12]
    existing = get_config(cfg_id) if payload.get("id") else None
    now = time.time()
    cfg = BenchConfig(
        id=cfg_id,
        name=str(payload.get("name") or "untitled"),
        description=str(payload.get("description") or ""),
        ensemble=list(payload.get("ensemble") or []),
        spo_models=list(payload.get("spo_models") or []),
        ner_quorum=str(payload.get("ner_quorum") or ""),
        all_videos=bool(payload.get("all_videos")),
        full=bool(payload.get("full")),
        ensemble_only=bool(payload.get("ensemble_only")),
        spo_only=bool(payload.get("spo_only")),
        no_consensus=bool(payload.get("no_consensus")),
        regen=bool(payload.get("regen")),
        sample_per_domain=(
            int(payload["sample_per_domain"])
            if payload.get("sample_per_domain") not in (None, "", 0)
            else None
        ),
        extra_args=list(payload.get("extra_args") or []),
        env_overrides={
            str(k): str(v)
            for k, v in (payload.get("env_overrides") or {}).items()
            if v not in (None, "")
        },
        created_at=existing["created_at"] if existing else now,
        updated_at=now,
    )
    # Defensive: re-create the dir each save. Module-load mkdir resolves
    # against the cached _REPO_ROOT, but if the path was wrong on first
    # import (stale process before a fix) the dir may not exist where we're
    # writing now. Cheap to retry.
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    p = _CONFIG_DIR / f"{cfg_id}.json"
    p.write_text(json.dumps(asdict(cfg), indent=2))
    return asdict(cfg)


def delete_config(config_id: str) -> bool:
    p = _CONFIG_DIR / f"{config_id}.json"
    if not p.exists():
        return False
    p.unlink()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Run management
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RunHandle:
    """In-memory tracking for a spawned harness subprocess."""

    run_id: str
    pid: int
    config: dict[str, Any]
    started_at: float
    log_path: Path
    proc: subprocess.Popen
    return_code: int | None = None


_runs: dict[str, RunHandle] = {}
_runs_lock = threading.Lock()
_MAX_CONCURRENT = 1


def _slugify_label(name: str) -> str:
    """Lowercase + alphanumeric/hyphen only — safe for an S3 run_id suffix.

    The harness builds run_ids as ``YYYY-MM-DD-HHMMSS-<label>`` and writes
    them as S3 prefixes; underscores / spaces / unicode would survive but
    confuse downstream tooling that lex-sorts run_ids. Falls back to
    ``"unnamed"`` if the input collapses to empty.
    """
    import re

    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return slug or "unnamed"


def _build_argv(cfg: dict[str, Any]) -> list[str]:
    """Translate a config dict → argv for ``tests/benchmark_harness.py``.

    The config ``name`` flows through as ``--label`` so the harness writes
    ``s3://dagster/bench/runs/<ts>-<slug>/`` and the State Inspector +
    BenchmarkReport pickers show the friendly name in the run_id itself.
    """
    argv = ["python", "tests/benchmark_harness.py"]
    if cfg.get("name"):
        argv += ["--label", _slugify_label(str(cfg["name"]))]
    if cfg.get("full"):
        argv.append("--full")
    if cfg.get("all_videos"):
        argv.append("--all-videos")
    if cfg.get("ensemble_only"):
        argv.append("--ensemble-only")
    if cfg.get("spo_only"):
        argv.append("--spo-only")
    if cfg.get("no_consensus"):
        argv.append("--no-consensus")
    if cfg.get("regen"):
        argv.append("--regen")
    if cfg.get("ensemble"):
        argv += ["--ensemble", ",".join(cfg["ensemble"])]
    if cfg.get("spo_models"):
        argv += ["--spo-models", ",".join(cfg["spo_models"])]
    if cfg.get("ner_quorum"):
        argv += ["--ner-quorum", cfg["ner_quorum"]]
    if cfg.get("extra_args"):
        argv += list(cfg["extra_args"])
    return argv


def list_runs() -> list[dict[str, Any]]:
    """Return all in-flight + completed runs the runner knows about."""
    out: list[dict[str, Any]] = []
    with _runs_lock:
        for h in _runs.values():
            # Refresh return code so stale entries flip from "running" → "done"
            if h.return_code is None and h.proc.poll() is not None:
                h.return_code = h.proc.returncode
            out.append(_handle_to_dict(h))
    out.sort(key=lambda r: r["started_at"], reverse=True)
    return out


def get_run(run_id: str) -> dict[str, Any] | None:
    with _runs_lock:
        h = _runs.get(run_id)
        if not h:
            return None
        if h.return_code is None and h.proc.poll() is not None:
            h.return_code = h.proc.returncode
        return _handle_to_dict(h)


def _handle_to_dict(h: RunHandle) -> dict[str, Any]:
    status = (
        "running"
        if h.return_code is None
        else ("ok" if h.return_code == 0 else "error")
    )
    return {
        "run_id": h.run_id,
        "pid": h.pid,
        "started_at": h.started_at,
        "status": status,
        "return_code": h.return_code,
        "config": h.config,
        "log_path": str(h.log_path),
    }


def start_run(config: dict[str, Any], sample_per_domain: int | None = None) -> dict[str, Any]:
    """Spawn the harness as a subprocess and return the run handle.

    Concurrency-capped at ``_MAX_CONCURRENT``. The harness inherits the
    current env (including LLM_API_KEY / DAGSTER_S3_* / CONGRESS_API_KEY)
    so the operator just needs them in their viewer-api process env.
    """
    with _runs_lock:
        active = sum(1 for h in _runs.values() if h.proc.poll() is None)
        if active >= _MAX_CONCURRENT:
            raise RuntimeError(
                f"max concurrent runs reached ({active}/{_MAX_CONCURRENT}) — "
                "stop the active run first"
            )

    argv = _build_argv(config)
    run_id = uuid.uuid4().hex[:12]
    # Defensive — the module-load mkdir may have created the dir against a
    # stale _REPO_ROOT that was later moved/cleaned. Cheap to retry.
    _RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _RUN_LOG_DIR / f"{run_id}.log"

    env = {**os.environ, "PYTHONPATH": "."}
    if sample_per_domain is not None:
        env["BENCH_SAMPLE_PER_DOMAIN"] = str(sample_per_domain)
    elif config.get("sample_per_domain"):
        env["BENCH_SAMPLE_PER_DOMAIN"] = str(config["sample_per_domain"])
    # Apply per-config env overrides last so they win over inherited values.
    # Empty strings are dropped at save_config time, but defensively skip
    # them here too in case they slip through an inline ``/run`` call.
    for k, v in (config.get("env_overrides") or {}).items():
        if v in (None, ""):
            continue
        env[str(k)] = str(v)

    log_fh = open(log_path, "wb")  # noqa: SIM115 — owned by Popen until exit
    log_fh.write(f"$ {shlex.join(argv)}\n".encode())
    log_fh.flush()

    proc = subprocess.Popen(  # noqa: S603 — argv is constructed, not user-string
        argv,
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        # New process group so we can SIGTERM the whole tree on stop
        start_new_session=True,
    )

    handle = RunHandle(
        run_id=run_id,
        pid=proc.pid,
        config=config,
        started_at=time.time(),
        log_path=log_path,
        proc=proc,
    )
    with _runs_lock:
        _runs[run_id] = handle

    return _handle_to_dict(handle)


def stop_run(run_id: str) -> bool:
    """SIGTERM the run's process group. Returns False if no such run."""
    with _runs_lock:
        h = _runs.get(run_id)
    if h is None:
        return False
    if h.proc.poll() is not None:
        return True  # already done; nothing to kill
    with contextlib.suppress(ProcessLookupError):
        os.killpg(os.getpgid(h.proc.pid), signal.SIGTERM)
    return True


def tail_log(run_id: str, max_bytes: int = 200_000) -> str:
    """Return the last ``max_bytes`` of the run's combined log."""
    with _runs_lock:
        h = _runs.get(run_id)
    if h is None or not h.log_path.exists():
        return ""
    size = h.log_path.stat().st_size
    with open(h.log_path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            data = b"... [truncated " + str(size - max_bytes).encode() + b" bytes] ...\n" + fh.read()
        else:
            data = fh.read()
    return data.decode("utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────────────────
# Models registry
# ─────────────────────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=1)
def list_models() -> dict[str, list[dict[str, str]]]:
    """Return the model registry grouped by tier so dropdowns can render it.

    Imports ``tests.benchmark_config`` lazily so the viewer-api doesn't
    take a hard dep on the test tree at import time. Cached because the
    import chain pulls in boto3/dagster and is multi-second on cold call —
    once it's warm subsequent reloads of the runner tab are instant.
    """
    import importlib

    bc = importlib.import_module("tests.benchmark_config")
    out: dict[str, list[dict[str, str]]] = {}
    for tier_name in ("ENCODER_MODELS", "EXTRACTION_MODELS", "CLOUD_MODELS"):
        models = getattr(bc, tier_name, [])
        out[tier_name] = [
            {
                "name": m.name,
                "model": m.model,
                "tags": list(m.tags),
            }
            for m in models
        ]
    # Tier1/Tier2 LLMs live under ALL_MODELS but not as their own list — pull
    # them out by tag intersection so the SPO dropdown sees them.
    all_models = getattr(bc, "ALL_MODELS", [])
    out["LLM_MODELS"] = [
        {"name": m.name, "model": m.model, "tags": list(m.tags)}
        for m in all_models
        if any(t in m.tags for t in ("tier1", "tier2"))
        and not any(t in m.tags for t in ("encoder", "extraction-specialist"))
    ]
    return out
