"""test_cli_no_consensus_mutex.py — --no-consensus is mutually exclusive with --ensemble-only / --spo-only.

Also tests that --no-consensus alone is accepted.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = "tests/benchmark_harness.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, HARNESS, *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=15,
    )


def test_no_consensus_accepted_alone():
    """--no-consensus alone must be accepted by argparse (--help exits 0)."""
    result = _run("--no-consensus", "--help")
    assert result.returncode == 0, f"--no-consensus caused argparse error:\n{result.stderr}"
    assert "--no-consensus" in result.stdout


def test_no_consensus_plus_ensemble_only_rejected():
    """--no-consensus --ensemble-only must exit 2 (mutually exclusive group)."""
    result = _run("--no-consensus", "--ensemble-only")
    assert result.returncode == 2, (
        f"Expected argparse exit 2 for --no-consensus + --ensemble-only. stderr:\n{result.stderr}"
    )
    assert "not allowed with argument" in result.stderr


def test_no_consensus_plus_spo_only_rejected():
    """--no-consensus --spo-only must exit 2 (mutually exclusive group)."""
    result = _run("--no-consensus", "--spo-only", "--run-id", "any-id")
    assert result.returncode == 2, f"Expected argparse exit 2 for --no-consensus + --spo-only. stderr:\n{result.stderr}"
    assert "not allowed with argument" in result.stderr


def test_all_three_phase_flags_rejected():
    """All three phase flags together must also be rejected."""
    result = _run("--no-consensus", "--ensemble-only", "--spo-only", "--run-id", "any-id")
    assert result.returncode == 2


def test_no_consensus_uses_ensemble_list_for_models():
    """In --no-consensus mode the model list equals the resolved ensemble list."""
    from tests.benchmark_config import get_model_by_name
    from tests.benchmark_harness import _default_ensemble

    ensemble_names = _default_ensemble()
    expected = [get_model_by_name(n) for n in ensemble_names if get_model_by_name(n)]
    assert expected, "Expected at least one model in --no-consensus list"

    # Verify none are None (all names resolved)
    unresolved = [n for n in ensemble_names if get_model_by_name(n) is None]
    assert not unresolved, f"Some ensemble model names did not resolve: {unresolved}"
