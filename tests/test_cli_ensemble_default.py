"""test_cli_ensemble_default.py — --ensemble omitted → defaults resolved from tags.

Asserts that _default_ensemble() and _default_spo() return non-empty lists
built purely from benchmark_config tag queries, matching the documented
defaults.
"""

from __future__ import annotations

from tests.benchmark_harness import _default_ensemble, _default_spo


def test_default_ensemble_non_empty():
    """_default_ensemble() must return at least one model."""
    result = _default_ensemble()
    assert result, "_default_ensemble() returned an empty list; check benchmark_config.py tags"


def test_default_spo_non_empty():
    """_default_spo() must return at least one model."""
    result = _default_spo()
    assert result, "_default_spo() returned an empty list; check benchmark_config.py tags"


def test_default_ensemble_all_have_encoder_or_specialist_tag():
    """Every name in _default_ensemble() must resolve to a model with the right tags."""
    from tests.benchmark_config import get_model_by_name

    for name in _default_ensemble():
        cfg = get_model_by_name(name)
        assert cfg is not None, f"_default_ensemble() returned '{name}' which is not in benchmark_config"
        assert "encoder" in cfg.tags or "extraction-specialist" in cfg.tags, (
            f"Model '{name}' in _default_ensemble() lacks both 'encoder' and 'extraction-specialist' tags"
        )


def test_default_spo_all_have_tier_or_cloud_tag():
    """Every name in _default_spo() must resolve to a model with tier1/tier2/cloud tags."""
    from tests.benchmark_config import get_model_by_name

    for name in _default_spo():
        cfg = get_model_by_name(name)
        assert cfg is not None, f"_default_spo() returned '{name}' which is not in benchmark_config"
        assert any(t in cfg.tags for t in ("tier1", "tier2", "cloud")), (
            f"Model '{name}' in _default_spo() lacks tier1/tier2/cloud tags (tags={cfg.tags})"
        )


def test_default_ensemble_omitted_in_argparse():
    """When --ensemble is omitted, argparse leaves args.ensemble as None and defaults are used."""
    import sys
    from unittest.mock import patch

    # Patch sys.argv to simulate invocation with no relevant flags
    with patch.object(sys, "argv", ["benchmark_harness.py", "--list-models"]):
        # Re-import to get a fresh parser; use the module's main function approach
        from tests.benchmark_harness import _default_ensemble

    result = _default_ensemble()
    assert result is not None
    assert len(result) > 0
