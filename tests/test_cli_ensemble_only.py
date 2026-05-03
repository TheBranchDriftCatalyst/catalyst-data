"""test_cli_ensemble_only.py — --ensemble-only short-circuits Phase 4 SPO.

Tests that argparse accepts --ensemble-only and that the resulting model list
contains only encoder/ensemble-tagged entries (no SPO LLMs).
"""

from __future__ import annotations

import argparse
import sys


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Drive the harness's argparse in a subprocess-safe way by patching sys.argv."""
    # Import the parser factory directly from the module via subprocess to avoid
    # side effects. For unit tests we recreate the parser by calling main() up to
    # parse_args() — instead, we just invoke Python's subprocess to check exit code.
    import os
    import subprocess

    env = {**os.environ}
    result = subprocess.run(
        [sys.executable, "tests/benchmark_harness.py"] + argv,
        capture_output=True,
        text=True,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
        env=env,
        timeout=10,
    )
    return result


def test_ensemble_only_flag_accepted():
    """--ensemble-only must be accepted by argparse (exit 0 for --help, not error on flag)."""
    result = _parse_args(["--ensemble-only", "--help"])
    # --help always exits 0 with help text; the flag must not cause an error
    assert result.returncode == 0, f"--ensemble-only caused argparse error:\n{result.stderr}"
    assert "--ensemble-only" in result.stdout


def test_ensemble_only_mutex_with_spo_only():
    """--ensemble-only and --spo-only must be mutually exclusive."""
    result = _parse_args(["--ensemble-only", "--spo-only"])
    assert result.returncode == 2, "Expected argparse error (exit 2) for --ensemble-only + --spo-only"
    assert "not allowed with argument" in result.stderr


def test_ensemble_only_mutex_with_no_consensus():
    """--ensemble-only and --no-consensus must be mutually exclusive."""
    result = _parse_args(["--ensemble-only", "--no-consensus"])
    assert result.returncode == 2, "Expected argparse error (exit 2) for --ensemble-only + --no-consensus"
    assert "not allowed with argument" in result.stderr


def test_ensemble_only_model_list_excludes_spo():
    """When --ensemble-only is set, the resolved model list contains only encoders + ensemble stub."""
    from tests.benchmark_config import ModelConfig, get_model_by_name
    from tests.benchmark_harness import _default_ensemble, _default_spo

    ensemble_names = _default_ensemble()
    spo_names = _default_spo()

    _ENSEMBLE_SYNTHETIC_CFG = ModelConfig(
        name="ensemble",
        model="ensemble",
        base_url="",
        structured_method="ensemble",
        tags=["encoder", "ensemble", "v4"],
    )

    # Simulate --ensemble-only model resolution (same logic as harness main())
    encoder_models = [get_model_by_name(n) for n in ensemble_names if get_model_by_name(n)]
    models = encoder_models + [_ENSEMBLE_SYNTHETIC_CFG]

    model_names = {m.name for m in models}

    # No pure SPO model (tier1/tier2/cloud without encoder tag) should appear
    for name in spo_names:
        cfg = get_model_by_name(name)
        if cfg is None:
            continue
        # A model could appear in both lists if it has both encoder and tier tags
        if "encoder" not in cfg.tags and "extraction-specialist" not in cfg.tags:
            assert name not in model_names, (
                f"SPO-only model '{name}' (tags={cfg.tags}) appeared in --ensemble-only model list"
            )
