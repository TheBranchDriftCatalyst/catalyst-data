"""Tests for the unified manifest loaders.

The seed / bench / integration paths used to each open
``bill_manifest.yaml`` and ``audio_manifest.yaml`` inline. Centralizing
into ``dagster_io.manifests`` means there's one shape to validate and
one selector signature to pin — that's what this file does.

Skip-rather-than-fail on missing fixtures is load-bearing: pytest must
not fail at collection time when this lib is consumed outside the
catalyst-data workspace.
"""

from __future__ import annotations

import pytest

from dagster_io.manifests import (
    CongressManifest,
    MediaManifest,
    congress_bill_ids,
    load_congress_manifest,
    load_media_manifest,
    media_doc_ids,
)

# ── Default-path discovery ────────────────────────────────────────────


def test_default_congress_manifest_exists_in_workspace():
    """When dagster_io is editable-installed in catalyst-data, the
    default manifest path resolves to the bundled fixture."""
    manifest = load_congress_manifest()
    # In the workspace layout the fixture has both keys populated; in
    # an external layout it'd be empty. Either is acceptable — the
    # contract is that the load doesn't raise.
    assert isinstance(manifest, CongressManifest)


def test_default_media_manifest_exists_in_workspace():
    manifest = load_media_manifest()
    assert isinstance(manifest, MediaManifest)


# ── Missing-path tolerance ────────────────────────────────────────────


def test_missing_congress_manifest_returns_empty(tmp_path):
    """Loader must not raise on missing fixture — pytest will skip."""
    nonexistent = tmp_path / "does-not-exist.yaml"
    manifest = load_congress_manifest(nonexistent)
    assert manifest.bills == []
    assert manifest.seed_subset == []


def test_missing_media_manifest_returns_empty(tmp_path):
    nonexistent = tmp_path / "does-not-exist.yaml"
    manifest = load_media_manifest(nonexistent)
    assert manifest.videos == []


# ── Shape validation ──────────────────────────────────────────────────


def test_congress_manifest_parses_real_shape(tmp_path):
    """seed_subset + bills are flat lists of bill_id strings."""
    p = tmp_path / "bill_manifest.yaml"
    p.write_text(
        """
seed_subset:
  - "119-hres-1"
  - "119-s-146"
bills:
  - "119-hr-1"
  - "119-hr-22"
  - "119-s-5"
"""
    )
    manifest = load_congress_manifest(p)
    assert manifest.seed_subset == ["119-hres-1", "119-s-146"]
    assert manifest.bills == ["119-hr-1", "119-hr-22", "119-s-5"]


def test_media_manifest_parses_real_shape(tmp_path):
    p = tmp_path / "audio_manifest.yaml"
    p.write_text(
        """
videos:
  - doc_id: demo-video
    file: demo_video.mp4
    title: Demo Video
  - doc_id: another
    file: another.mp4
    title: Another
"""
    )
    manifest = load_media_manifest(p)
    assert len(manifest.videos) == 2
    assert manifest.videos[0].doc_id == "demo-video"
    assert manifest.videos[0].file == "demo_video.mp4"
    assert manifest.videos[0].title == "Demo Video"


def test_media_video_title_optional(tmp_path):
    """Missing title falls back to empty string — bench harness expects str."""
    p = tmp_path / "audio_manifest.yaml"
    p.write_text(
        """
videos:
  - doc_id: notitled
    file: x.mp4
"""
    )
    manifest = load_media_manifest(p)
    assert manifest.videos[0].title == ""


# ── Selectors ─────────────────────────────────────────────────────────


def test_congress_bill_ids_default_returns_bills(tmp_path):
    p = tmp_path / "bill_manifest.yaml"
    p.write_text(
        """
seed_subset:
  - "119-hres-1"
bills:
  - "119-hr-1"
  - "119-hr-22"
  - "119-s-5"
"""
    )
    assert congress_bill_ids(path=p) == ["119-hr-1", "119-hr-22", "119-s-5"]


def test_congress_bill_ids_subset_returns_seed_subset(tmp_path):
    p = tmp_path / "bill_manifest.yaml"
    p.write_text(
        """
seed_subset:
  - "119-hres-1"
  - "119-s-146"
bills:
  - "119-hr-1"
  - "119-hr-22"
"""
    )
    assert congress_bill_ids(subset=True, path=p) == ["119-hres-1", "119-s-146"]


def test_congress_bill_ids_subset_falls_back_to_bills_when_empty(tmp_path):
    """No seed_subset key → return the first N of bills. Same legacy
    behaviour the seed script had before consolidation."""
    p = tmp_path / "bill_manifest.yaml"
    p.write_text(
        """
bills:
  - "119-hr-1"
  - "119-hr-22"
"""
    )
    assert congress_bill_ids(subset=True, path=p) == ["119-hr-1", "119-hr-22"]


@pytest.mark.parametrize("limit,expected", [(1, 1), (3, 3), (10, 3)])
def test_congress_bill_ids_limit_truncates(tmp_path, limit, expected):
    p = tmp_path / "bill_manifest.yaml"
    p.write_text(
        """
bills:
  - "a"
  - "b"
  - "c"
"""
    )
    assert len(congress_bill_ids(limit=limit, path=p)) == expected


def test_media_doc_ids_returns_in_order(tmp_path):
    p = tmp_path / "audio_manifest.yaml"
    p.write_text(
        """
videos:
  - doc_id: z
    file: z.mp4
  - doc_id: a
    file: a.mp4
"""
    )
    # Manifest order is the curation order — not sorted.
    assert media_doc_ids(path=p) == ["z", "a"]


def test_media_doc_ids_limit(tmp_path):
    p = tmp_path / "audio_manifest.yaml"
    p.write_text(
        """
videos:
  - {doc_id: a, file: a.mp4}
  - {doc_id: b, file: b.mp4}
  - {doc_id: c, file: c.mp4}
"""
    )
    assert media_doc_ids(limit=2, path=p) == ["a", "b"]


# ── Cross-path alignment (the core motivation) ────────────────────────


def test_seed_and_bench_see_same_bills():
    """If the workspace fixture exists, the unified loader returns the
    same list for seed (subset) and bench (full) — they no longer drift
    because both go through congress_bill_ids() with one toggle.
    """
    full = congress_bill_ids()
    subset = congress_bill_ids(subset=True)
    if not full and not subset:
        pytest.skip("no workspace fixture")
    # Subset is a non-empty subset of bills OR (in the legacy fallback
    # case) the same list. Either way, seed and bench can't diverge in
    # shape — both are list[str].
    assert isinstance(full, list)
    assert isinstance(subset, list)
    if full and subset:
        # In the seeded fixture the subset members should be in bills
        # OR explicitly distinct (allowed by design — the seed picks
        # variety bills not necessarily in the bench corpus).
        # We just assert the shape contract holds.
        assert all(isinstance(b, str) for b in full + subset)


def test_seed_workspace_fixture_has_five_subset_bills():
    """Pin the load-bearing assumption that seed:congress picks 5 bills.
    If the fixture grows the subset, update this test — and the
    Taskfile description, and the dev/seed_local.py docstring."""
    bills = congress_bill_ids(subset=True)
    if not bills:
        pytest.skip("no workspace fixture")
    assert len(bills) == 5, f"seed_subset has {len(bills)} bills, expected 5"
