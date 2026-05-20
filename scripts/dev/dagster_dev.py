#!/usr/bin/env python3
"""Launch ``dagster dev`` with env loaded from the local k8s manifests.

Why a wrapper instead of Tilt's ``serve_env``: Tilt evaluates the Tiltfile
once, captures the env dict at that moment, then reuses it on every
restart. Edits to ``k8s/local/dagster-dev-config.yaml`` don't bust that
cache without a real Tiltfile change, so stale vars survive
``tilt trigger`` and ``tilt up``.

This script reads the ConfigMap + Secret YAMLs at *process* start,
resolves relative paths, provisions ``$CATALYST_DATA_ROOT`` (mirror of
the prod NFS layout), and execs ``dagster dev``. Each launch sees the
current on-disk env — no caching layer.

Invoked from the Tiltfile as ``uv run -- python scripts/dev/dagster_dev.py``.
Also runnable standalone for non-Tilt sessions.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# scripts/dev/dagster_dev.py → catalyst-data
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "k8s/local/dagster-dev-config.yaml"
SECRETS_PATH = REPO_ROOT / "k8s/local/dagster-dev-secrets.yaml"

# Env vars that hold project-relative paths (need promotion to absolute).
# Keep in sync with anything in the ConfigMap that's a directory the
# code reads at module-import time.
_REL_PATH_VARS = ("CATALYST_DATA_ROOT",)

# Vars that earlier dev rails set but the code no longer reads. Scrubbed
# so a stale shell or a long-lived Tilt process can't leak them in and
# mask real config bugs. Add the name here when you retire an env knob.
_DEPRECATED_VARS = (
    "CATALYST_MEDIA_ROOT_METUBE",
    "CATALYST_MEDIA_ROOT_TUBESYNC",
    "WHISPER_MODEL_CACHE",
    "PROMPT_REGISTRY_DIR",
)

CODE_LOCATIONS = ("media_ingest", "congress_data", "open_leaks")


def _load_yaml_data(path: Path, section: str) -> dict[str, str]:
    """Return the data/stringData mapping from a k8s manifest, or empty."""
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text()) or {}
    section_data = doc.get(section) or {}
    return {str(k): str(v) for k, v in section_data.items()}


def _resolve_relative_paths(env: dict[str, str]) -> None:
    """Promote project-relative path vars to absolute paths in-place."""
    for var in _REL_PATH_VARS:
        val = env.get(var)
        if val and not val.startswith("/"):
            env[var] = str(REPO_ROOT / val)


def _provision_data_root(env: dict[str, str]) -> None:
    """Create dev-time mirror of the prod /data layout under CATALYST_DATA_ROOT.

    Idempotent. Symlinks fixtures into tubesync only — the test
    corpus is tubesync-sourced (all doc IDs are `media-tubesync-*`),
    so symlinking the same fixtures dir into BOTH metube and tubesync
    makes discovery double-count every file. metube is left as an
    empty dir so the discovery code finds the path but yields nothing.

    whisper-models is a writable dir for HF/faster-whisper downloads.
    """
    data_root = env.get("CATALYST_DATA_ROOT")
    if not data_root:
        return
    root = Path(data_root)
    fixtures = REPO_ROOT / "packages/media-ingest/tests/fixtures"
    (root / "whisper-models").mkdir(parents=True, exist_ok=True)
    (root / "metube").mkdir(exist_ok=True)  # empty placeholder

    tubesync = root / "tubesync"
    if tubesync.is_symlink():
        tubesync.unlink()
    elif tubesync.exists() and not tubesync.is_symlink():
        return  # don't clobber a real mount
    tubesync.symlink_to(fixtures)


def _build_env() -> dict[str, str]:
    """Compose final env: shell env <- config <- secrets (later wins)."""
    env = dict(os.environ)
    for var in _DEPRECATED_VARS:
        env.pop(var, None)
    env.update(_load_yaml_data(CONFIG_PATH, "data"))
    env.update(_load_yaml_data(SECRETS_PATH, "stringData"))
    # Default EMBEDDING_BASE_URL to LLM_BASE_URL when ConfigMap omits it
    # (the Definitions in media_ingest/__init__.py reads both).
    env.setdefault("EMBEDDING_BASE_URL", env.get("LLM_BASE_URL", ""))
    _resolve_relative_paths(env)
    return env


def main() -> int:
    env = _build_env()
    _provision_data_root(env)

    if not SECRETS_PATH.exists():
        print(
            f"WARN: {SECRETS_PATH.relative_to(REPO_ROOT)} missing — copy the "
            ".example and fill in. LLM / Congress / HF calls will fail until you do.",
            file=sys.stderr,
        )

    cmd = ["dagster", "dev"]
    for loc in CODE_LOCATIONS:
        cmd.extend(["-m", loc])
    cmd.extend(sys.argv[1:])

    # execvp keeps env (we already merged into it) and replaces the
    # Python process, so signals from Tilt go straight to dagster.
    os.execvpe(cmd[0], cmd, env)
    return 0  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
