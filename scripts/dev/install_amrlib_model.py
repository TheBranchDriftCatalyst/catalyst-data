#!/usr/bin/env python3
"""One-time bootstrap for amrlib's pre-trained STOG parser checkpoint.

The AMR-as-spine extraction path (`AmrToAssertionNode`) calls
`amrlib.load_stog_model()`, which expects a model directory at
``<site-packages>/amrlib/data/model_stog/``. amrlib does not ship the
weights themselves — they live as GitHub release assets on
``bjascob/amrlib-models``. Without them, every sentence parse silently
fails with ``FileNotFoundError: model_stog`` and ``bill_assertions``
ends up empty on `task seed:congress --with-gold`.

This script:

1. Detects existing install at ``<site-packages>/amrlib/data/model_stog/``
   and exits idempotently if present.
2. Downloads ``model_parse_xfm_bart_base-v0_1_0.tar.gz`` (~492 MB) to a
   workspace-local cache at ``.test-output/.amr-models/`` so re-runs
   across recreated venvs skip the network hop.
3. Extracts the tarball and renames the extracted directory to
   ``model_stog`` (the path `load_stog_model()` looks for by default).
4. Validates the install by importing amrlib and calling
   ``load_stog_model()``.

Usage::

    uv run python scripts/dev/install_amrlib_model.py
    # or:
    task install:amr

Stdlib-only — no new deps. Re-runs are fast (cache hit → just verify).
"""

from __future__ import annotations

import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Pinned model release
# ---------------------------------------------------------------------------
# Source: https://github.com/bjascob/amrlib-models/releases (queried via
# `gh api repos/bjascob/amrlib-models/releases` on 2026-05-19). This is the
# recommended STOG (sentence-to-graph) parser per amrlib's README — the
# `parse_xfm_bart_base` variant is the smallest viable production checkpoint
# (~492 MB vs. ~1.4 GB for `bart_large`) and is what amrlib's own examples
# install. Bumping to a newer release? Verify by:
#   gh api repos/bjascob/amrlib-models/releases --jq '.[].assets[].name'
# and update both _MODEL_URL and _MODEL_TARBALL_TOPDIR below.
_MODEL_RELEASE_TAG = "parse_xfm_bart_base-v0_1_0"
_MODEL_TARBALL_NAME = "model_parse_xfm_bart_base-v0_1_0.tar.gz"
_MODEL_URL = f"https://github.com/bjascob/amrlib-models/releases/download/{_MODEL_RELEASE_TAG}/{_MODEL_TARBALL_NAME}"
# Directory name inside the tarball after extraction. The tarball contains a
# single top-level directory matching the release artifact's basename (sans
# `.tar.gz`); we rename this to `model_stog` to satisfy amrlib's defaults.
_MODEL_TARBALL_TOPDIR = "model_parse_xfm_bart_base-v0_1_0"

# Files amrlib expects inside `model_stog/`. Used to validate that an existing
# install is actually complete (not a half-finished extraction). Conservative
# list — amrlib's inference loader will raise if any of these is missing.
_EXPECTED_FILES = ("model.safetensors", "config.json")
# Fallback name from older releases — amrlib also accepts `pytorch_model.bin`.
_EXPECTED_FILES_ALT = ("pytorch_model.bin", "config.json")

# Workspace-local tarball cache. Persists across venv recreations so a fresh
# `uv sync` doesn't force a redownload. Aligns with existing `.test-output/`
# convention (gitignored, regenerable).
ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = ROOT / ".test-output" / ".amr-models"


def _site_packages_amrlib_data() -> Path:
    """Locate amrlib's data dir inside the active interpreter's site-packages.

    Imports amrlib (must already be installed via `uv sync`) and reads its
    `defaults.data_dir` — the same path `load_stog_model()` consults. This
    avoids hard-coding a python3.12 path that would break on a 3.13 bump.
    """
    try:
        import amrlib  # noqa: F401
        from amrlib import defaults
    except ImportError as e:
        print(
            f"ERROR: amrlib is not installed in the active interpreter ({sys.executable}).\n"
            f"       Run `uv sync` first. ({e})",
            file=sys.stderr,
        )
        sys.exit(2)
    # `unidecode` is a transitive runtime dep of amrlib's parse_xfm inference
    # path (penman_serializer.py) but is NOT declared by amrlib itself. If
    # missing, validation will fail with a confusing ModuleNotFoundError after
    # the 500 MB download succeeds — surface it up front instead.
    try:
        import unidecode  # noqa: F401
    except ImportError:
        print(
            "ERROR: amrlib's inference path needs `unidecode` (transitive dep "
            "not declared by amrlib==0.8.1).\n"
            "       Install it into the active venv: `uv pip install unidecode`",
            file=sys.stderr,
        )
        sys.exit(2)
    return Path(defaults.data_dir)


def _is_installed(model_stog_dir: Path) -> bool:
    """True iff `model_stog/` exists and contains the expected weights."""
    if not model_stog_dir.is_dir():
        return False
    have_primary = all((model_stog_dir / f).exists() for f in _EXPECTED_FILES)
    have_alt = all((model_stog_dir / f).exists() for f in _EXPECTED_FILES_ALT)
    return have_primary or have_alt


def _download(url: str, dest: Path) -> None:
    """Stream-download `url` to `dest`, printing progress every ~50 MB."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"download: {url}")
    print(f"     ->  {dest}")
    with urllib.request.urlopen(url) as resp:  # noqa: S310 — pinned github.com URL
        total = int(resp.headers.get("Content-Length", "0") or 0)
        total_mb = total / (1024 * 1024) if total else None
        downloaded = 0
        chunk_size = 1 << 20  # 1 MB
        next_report = 50 * (1 << 20)  # 50 MB
        with tmp.open("wb") as fh:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                if downloaded >= next_report:
                    mb = downloaded / (1024 * 1024)
                    if total_mb:
                        pct = 100.0 * downloaded / total
                        print(f"     ... {mb:.0f} MB / {total_mb:.0f} MB ({pct:.0f}%)")
                    else:
                        print(f"     ... {mb:.0f} MB")
                    next_report += 50 * (1 << 20)
    tmp.replace(dest)
    final_mb = dest.stat().st_size / (1024 * 1024)
    print(f"download: done ({final_mb:.0f} MB)")


def _extract(tarball: Path, data_dir: Path, model_stog_dir: Path) -> None:
    """Extract `tarball` under `data_dir`, then rename the top-level dir to
    `model_stog` (atomic via `Path.replace`)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    # If a prior incomplete attempt left a half-extracted top-level dir, nuke it.
    extracted_top = data_dir / _MODEL_TARBALL_TOPDIR
    if extracted_top.exists():
        print(f"extract: removing stale {extracted_top}")
        shutil.rmtree(extracted_top)

    print(f"extract: {tarball.name} -> {data_dir}")
    with tarfile.open(tarball, "r:gz") as tf:
        # Validate the tarball has the expected top-level directory before
        # extracting so a bad/renamed asset surfaces as a clear error.
        names = tf.getnames()
        roots = {n.split("/", 1)[0] for n in names if n}
        if _MODEL_TARBALL_TOPDIR not in roots:
            print(
                f"ERROR: tarball top-level dirs {sorted(roots)} do not include "
                f"expected '{_MODEL_TARBALL_TOPDIR}'. The amrlib-models release "
                f"layout may have changed — update _MODEL_TARBALL_TOPDIR.",
                file=sys.stderr,
            )
            sys.exit(3)
        # Python 3.12+ requires an explicit filter; `data` is the safe default
        # that strips ownership/perms but allows regular files + dirs.
        tf.extractall(data_dir, filter="data")

    if not extracted_top.is_dir():
        print(f"ERROR: extraction produced no {_MODEL_TARBALL_TOPDIR}/", file=sys.stderr)
        sys.exit(3)

    # If `model_stog/` already exists (e.g. a previous partial install), remove
    # it so the rename can land.
    if model_stog_dir.exists():
        print(f"extract: removing existing {model_stog_dir}")
        if model_stog_dir.is_symlink() or model_stog_dir.is_file():
            model_stog_dir.unlink()
        else:
            shutil.rmtree(model_stog_dir)
    print(f"extract: rename {_MODEL_TARBALL_TOPDIR}/ -> model_stog/")
    extracted_top.replace(model_stog_dir)


def _validate(model_stog_dir: Path) -> None:
    """Import amrlib and call `load_stog_model()` to confirm the install
    actually loads. Surfaces any deserialization error with a clear message."""
    print(f"validate: load_stog_model({model_stog_dir})")
    try:
        import amrlib

        amrlib.load_stog_model()
    except Exception as e:  # broad: amrlib raises a variety of exceptions
        print(
            f"ERROR: amrlib.load_stog_model() failed after install: {type(e).__name__}: {e}\n"
            f"       Check {model_stog_dir} contents and re-run.",
            file=sys.stderr,
        )
        sys.exit(4)
    print("validate: OK — amrlib can load the STOG model")


def main() -> int:
    data_dir = _site_packages_amrlib_data()
    model_stog_dir = data_dir / "model_stog"

    if _is_installed(model_stog_dir):
        print(f"already installed: {model_stog_dir}")
        print("                   (delete the directory to force re-install)")
        return 0

    print(f"target: {model_stog_dir}")
    print(f"cache:  {_CACHE_DIR}")

    tarball = _CACHE_DIR / _MODEL_TARBALL_NAME
    if tarball.exists():
        size_mb = tarball.stat().st_size / (1024 * 1024)
        print(f"download: cache hit ({tarball.name}, {size_mb:.0f} MB) — skipping fetch")
    else:
        _download(_MODEL_URL, tarball)

    _extract(tarball, data_dir, model_stog_dir)
    _validate(model_stog_dir)

    print(f"\nInstalled amrlib STOG model at {model_stog_dir}")
    print(f"Cached tarball at {tarball} (~{tarball.stat().st_size / (1024 * 1024):.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
