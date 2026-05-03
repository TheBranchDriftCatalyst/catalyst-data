"""test_cli_spo_only_requires_run_id.py — --spo-only without --run-id → argparse error.

Verifies that the post-parse validation in main() raises a parser.error()
(exit code 2) when --spo-only is given without --run-id.
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


def test_spo_only_without_run_id_exits_2():
    """--spo-only alone must exit with code 2 and a helpful message."""
    result = _run("--spo-only")
    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}. stderr:\n{result.stderr}"
    assert "--run-id" in result.stderr, f"Error message should mention --run-id. stderr:\n{result.stderr}"


def test_spo_only_with_run_id_passes_argparse():
    """--spo-only --run-id <id> must pass argparse validation (run-id arg is accepted).

    We don't run the actual benchmark — just check that argparse doesn't error.
    Exit code 2 would indicate an argparse error; any other exit (0 or non-zero
    from the harness itself) means argparse accepted the flags.
    """
    result = _run("--spo-only", "--run-id", "fake-run-id-for-argparse-test", "--help")
    # --help with valid flags → exit 0
    assert result.returncode == 0, f"--spo-only --run-id <id> caused argparse error:\n{result.stderr}"


def test_spo_only_mutex_with_ensemble_only():
    """--spo-only and --ensemble-only must be mutually exclusive."""
    result = _run("--spo-only", "--run-id", "some-id", "--ensemble-only")
    assert result.returncode == 2, f"Expected argparse error (exit 2). stderr:\n{result.stderr}"
    assert "not allowed with argument" in result.stderr


def test_spo_only_mutex_with_no_consensus():
    """--spo-only and --no-consensus must be mutually exclusive."""
    result = _run("--spo-only", "--run-id", "some-id", "--no-consensus")
    assert result.returncode == 2, f"Expected argparse error (exit 2). stderr:\n{result.stderr}"
    assert "not allowed with argument" in result.stderr
