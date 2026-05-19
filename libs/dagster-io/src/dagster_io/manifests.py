"""Single source of truth for the per-domain test/seed/bench corpus.

Three Dagster code locations ship with fixtures that pin which bills /
videos / cables make up the canonical benchmark corpus:

  - ``packages/congress-data/tests/fixtures/bill_manifest.yaml``
       * ``seed_subset`` — 5 hand-curated variety bills consumed by
         ``task seed:congress``.
       * ``bills`` — full ~30-bill benchmark corpus consumed by
         ``task bench:chunks:regen:congress``.
  - ``packages/media-ingest/tests/fixtures/audio_manifest.yaml``
       * ``videos`` — list of ``{doc_id, file, title}`` entries; the
         seed iterates the subset that has a populated diarization
         cache, the bench harness iterates all of them.
  - ``packages/open-leaks/tests/fixtures/cablegate_sample.csv``
       * 50 WikiLeaks cables; the unpartitioned ``leak_chunks`` asset
         reads the whole CSV.

Before this module, each caller (seed, bench, integration tests) opened
the YAML inline. That meant seed could read ``seed_subset`` while bench
read ``bills`` without sharing a loader — adding a third selection knob
required touching N call sites.

Centralizing here gives one Pydantic-validated shape per manifest and a
single set of selectors. Adding a new corpus subset (e.g. an LLM-only
``hot_path`` slice) is a one-line addition to the Manifest class plus a
selector helper, instead of editing every script.

Path discovery: defaults assume dagster_io is editable-installed inside
``catalyst-data/libs/dagster-io`` — ``_repo_root()`` walks up from
``__file__`` to find the catalyst-data root. Callers in a different
layout pass an explicit ``path=`` instead.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

__all__ = [
    "CongressManifest",
    "MediaManifest",
    "MediaVideo",
    "congress_bill_ids",
    "load_congress_manifest",
    "load_media_manifest",
    "media_doc_ids",
]


# ── Path discovery ────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Locate the catalyst-data repo root from this file's location.

    libs/dagster-io/src/dagster_io/manifests.py → catalyst-data
    That's four parents up. If dagster_io is ever published as a wheel
    and installed in site-packages this default breaks — callers in
    that mode must pass an explicit ``path=``.
    """
    return Path(__file__).resolve().parents[4]


def _default_congress_manifest_path() -> Path:
    return _repo_root() / "packages" / "congress-data" / "tests" / "fixtures" / "bill_manifest.yaml"


def _default_media_manifest_path() -> Path:
    return _repo_root() / "packages" / "media-ingest" / "tests" / "fixtures" / "audio_manifest.yaml"


# ── Shapes ────────────────────────────────────────────────────────────


class CongressManifest(BaseModel):
    """Parsed view of ``bill_manifest.yaml``.

    Both ``seed_subset`` and ``bills`` are flat lists of bill_id strings
    (format: ``<congress>-<bill_type>-<number>``, e.g. ``119-s-146``).
    YAML inline comments alongside each bill_id document the policy
    area + chamber + size — those don't survive yaml.safe_load and
    that's fine; the comments are for human curators, not the loader.
    """

    seed_subset: list[str] = Field(default_factory=list)
    bills: list[str] = Field(default_factory=list)


class MediaVideo(BaseModel):
    """One entry in ``audio_manifest.yaml::videos``."""

    doc_id: str
    file: str
    title: str = ""


class MediaManifest(BaseModel):
    """Parsed view of ``audio_manifest.yaml``."""

    videos: list[MediaVideo] = Field(default_factory=list)


# ── Loaders ───────────────────────────────────────────────────────────


def load_congress_manifest(path: Path | None = None) -> CongressManifest:
    """Read + validate ``bill_manifest.yaml``.

    Returns an empty manifest (no error) when the file is missing — same
    behaviour as the pre-consolidation call sites, which lets pytest
    skip-rather-than-fail when the fixture isn't present.
    """
    p = path or _default_congress_manifest_path()
    if not p.exists():
        return CongressManifest()
    raw = yaml.safe_load(p.read_text()) or {}
    return CongressManifest.model_validate(raw)


def load_media_manifest(path: Path | None = None) -> MediaManifest:
    """Read + validate ``audio_manifest.yaml``."""
    p = path or _default_media_manifest_path()
    if not p.exists():
        return MediaManifest()
    raw = yaml.safe_load(p.read_text()) or {}
    return MediaManifest.model_validate(raw)


# ── Selectors ─────────────────────────────────────────────────────────


def congress_bill_ids(
    *,
    subset: bool = False,
    limit: int | None = None,
    path: Path | None = None,
) -> list[str]:
    """Return the canonical bill_id list.

    Args:
        subset: When True, return ``seed_subset`` (5 hand-curated bills
            for ``task seed:congress``); falls back to ``bills`` when
            seed_subset is empty. When False, return the full ``bills``
            list (the benchmark corpus).
        limit: Truncate to the first N entries after subset selection.
            None means "no limit".
        path: Override the manifest path (defaults to the bundled
            fixture under packages/congress-data/tests/fixtures/).

    Returns:
        A list of bill_id strings in declaration order. Empty list when
        the manifest is missing OR when both seed_subset and bills are
        empty — callers should handle that case (skip / no-op).
    """
    manifest = load_congress_manifest(path)
    bill_ids = (manifest.seed_subset or manifest.bills) if subset else manifest.bills
    if limit is not None:
        bill_ids = bill_ids[:limit]
    return list(bill_ids)


def media_doc_ids(
    *,
    limit: int | None = None,
    path: Path | None = None,
) -> list[str]:
    """Return the canonical media doc_id list (in manifest order)."""
    manifest = load_media_manifest(path)
    doc_ids = [v.doc_id for v in manifest.videos]
    if limit is not None:
        doc_ids = doc_ids[:limit]
    return doc_ids
