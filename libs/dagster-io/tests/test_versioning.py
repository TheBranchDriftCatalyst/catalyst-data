"""Tests for module content hashing code versioning."""

from __future__ import annotations

import dagster_io.concordance as concordance_mod
import dagster_io.extraction as extraction_mod
from dagster_io.versioning import code_version_from_modules


def test_returns_12_char_hex():
    """code_version is a 12-char hex digest."""
    v = code_version_from_modules(concordance_mod)
    assert len(v) == 12
    assert all(c in "0123456789abcdef" for c in v)


def test_deterministic():
    """Same modules produce same hash."""
    v1 = code_version_from_modules(concordance_mod)
    v2 = code_version_from_modules(concordance_mod)
    assert v1 == v2


def test_different_modules_different_hash():
    """Different modules produce different hashes."""
    v1 = code_version_from_modules(concordance_mod)
    v2 = code_version_from_modules(extraction_mod)
    assert v1 != v2


def test_multi_module_hash():
    """Multi-module hash differs from single-module hash."""
    v1 = code_version_from_modules(concordance_mod)
    v2 = code_version_from_modules(concordance_mod, extraction_mod)
    assert v1 != v2


def test_module_order_invariant():
    """Module order doesn't affect hash (sorted internally)."""
    v1 = code_version_from_modules(concordance_mod, extraction_mod)
    v2 = code_version_from_modules(extraction_mod, concordance_mod)
    assert v1 == v2


def test_empty_modules():
    """No modules returns a hash (of empty content)."""
    v = code_version_from_modules()
    assert len(v) == 12
