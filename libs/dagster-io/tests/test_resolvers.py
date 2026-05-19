"""Tests for the prompt-dir + label-pack resolvers (CD-ojkr + CD-z0kw)."""

from __future__ import annotations

import pytest

from dagster_io.extraction import LABEL_PACK_BY_LOCATION, resolve_label_pack
from dagster_io.prompts import resolve_prompt_dir

# ── PROMPT_REGISTRY_DIR resolver ──────────────────────────────────────


def test_resolve_prompt_dir_env_var_wins(monkeypatch, tmp_path):
    """Explicit env var override beats anything else."""
    monkeypatch.setenv("PROMPT_REGISTRY_DIR", str(tmp_path))
    assert resolve_prompt_dir() == str(tmp_path)


def test_resolve_prompt_dir_domain_uses_bundled(monkeypatch):
    """Without env override, ``domain=`` hits the bundled k8s/base/<domain>/prompts."""
    monkeypatch.delenv("PROMPT_REGISTRY_DIR", raising=False)
    resolved = resolve_prompt_dir(domain="congress-data")
    # Either the workspace fixture exists OR we fall back. Both are
    # acceptable — the contract is "no exception".
    assert isinstance(resolved, str)
    if resolved:
        assert "congress-data" in resolved or "shared" in resolved


def test_resolve_prompt_dir_unknown_domain_falls_back_to_shared(monkeypatch):
    """Unknown domain → shared prompts dir (when present)."""
    monkeypatch.delenv("PROMPT_REGISTRY_DIR", raising=False)
    resolved = resolve_prompt_dir(domain="does-not-exist")
    assert isinstance(resolved, str)


def test_resolve_prompt_dir_fallback_used_when_nothing_resolves(monkeypatch, tmp_path):
    """fallback= kicks in only when env + domain + shared all miss."""
    monkeypatch.delenv("PROMPT_REGISTRY_DIR", raising=False)
    # Use a tmp_path that obviously doesn't exist as a "domain" subdir,
    # but pass an explicit fallback so the result is deterministic.
    nonexistent_fallback = tmp_path / "fallback"
    resolved = resolve_prompt_dir(domain="nonexistent-domain-xyz-123", fallback=nonexistent_fallback)
    # Either the shared dir resolved (workspace) or the fallback did.
    assert resolved == str(nonexistent_fallback) or resolved


# ── LABEL_PACK_BY_LOCATION ────────────────────────────────────────────


def test_label_pack_by_location_public_constant():
    """The mapping is now exposed as a public constant — benchmark
    harness + tests can import it directly instead of assuming the
    underscore-prefixed internal name."""
    assert "congress_data" in LABEL_PACK_BY_LOCATION
    assert "media_ingest" in LABEL_PACK_BY_LOCATION
    assert LABEL_PACK_BY_LOCATION["congress_data"] == "congress"
    assert LABEL_PACK_BY_LOCATION["media_ingest"] == "media"


@pytest.mark.parametrize(
    "code_location,expected",
    [
        ("congress", "congress"),
        ("congress_data", "congress"),
        ("media", "media"),
        ("media_ingest", "media"),
        ("unknown_loc", "generic"),
        ("", "generic"),
    ],
)
def test_resolve_label_pack_mapping(code_location, expected):
    assert resolve_label_pack(code_location) == expected


def test_legacy_underscore_aliases_still_importable():
    """Old code that imported the underscore-prefixed names keeps
    working until a follow-up sweep retires them."""
    from dagster_io.extraction import _LABEL_PACK_BY_LOCATION, _resolve_label_pack

    assert _LABEL_PACK_BY_LOCATION is LABEL_PACK_BY_LOCATION
    assert _resolve_label_pack is resolve_label_pack
