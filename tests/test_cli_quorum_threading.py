"""test_cli_quorum_threading.py — --ensemble-quorum K is validated + threaded to ConsensusNode.

Tests:
1. --ensemble-quorum with valid K passes argparse.
2. --ensemble-quorum with K > N (number of ensemble models) is rejected.
3. --ensemble-quorum with K < 1 is rejected.
4. _build_ensemble_pipeline_for_phase_a receives and passes quorum through to
   build_ensemble_pipeline (mocked to capture kwargs).
5. _phase_a_build_cluster_cache passes quorum to the pipeline builder.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


# ── argparse-level tests ──────────────────────────────────────────────────────


def test_quorum_valid_accepted():
    """--ensemble-quorum 2 with help must exit 0 (flag accepted by argparse)."""
    result = _run("--ensemble-quorum", "2", "--help")
    assert result.returncode == 0, f"Unexpected error:\n{result.stderr}"


def test_quorum_zero_rejected_post_parse():
    """--ensemble-quorum 0 violates 1 ≤ K ≤ N and must exit 2."""
    # Use --ensemble-only to trigger model resolution without running anything
    result = _run("--ensemble-only", "--ensemble-quorum", "0")
    assert result.returncode == 2, f"Expected exit 2 for quorum=0. stderr:\n{result.stderr}"
    assert "quorum" in result.stderr.lower() or "range" in result.stderr.lower(), (
        f"Error message should mention quorum. stderr:\n{result.stderr}"
    )


def test_quorum_exceeds_n_rejected():
    """--ensemble-quorum N+100 violates 1 ≤ K ≤ N and must exit 2."""
    result = _run("--ensemble-only", "--ensemble-quorum", "9999")
    assert result.returncode == 2, f"Expected exit 2 for quorum=9999 (> N). stderr:\n{result.stderr}"
    assert "quorum" in result.stderr.lower() or "range" in result.stderr.lower(), (
        f"Error message should mention quorum. stderr:\n{result.stderr}"
    )


# ── Unit-level threading tests ────────────────────────────────────────────────


def test_build_ensemble_pipeline_receives_quorum():
    """_build_ensemble_pipeline_for_phase_a(encoder_cfgs, quorum=2) passes quorum to build_ensemble_pipeline."""
    import tests.benchmark_harness as harness_module
    from tests.benchmark_config import ENCODER_MODELS

    encoder_cfgs = [m for m in ENCODER_MODELS if "encoder" in m.tags]
    if not encoder_cfgs:
        pytest.skip("No encoder models configured in benchmark_config.py")

    captured_kwargs: dict = {}

    def capturing_bep(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    # Patch the name inside catalyst_exgraph.pipeline at the module level so
    # _build_ensemble_pipeline_for_phase_a's local import picks up the mock.
    import catalyst_exgraph.pipeline as pipeline_module

    with patch.object(pipeline_module, "build_ensemble_pipeline", side_effect=capturing_bep):
        harness_module._build_ensemble_pipeline_for_phase_a(encoder_cfgs[:1], quorum=2)

    assert "quorum" in captured_kwargs, (
        f"build_ensemble_pipeline was not called with quorum kwarg. Got kwargs: {captured_kwargs}"
    )
    assert captured_kwargs["quorum"] == 2, f"Expected quorum=2, got quorum={captured_kwargs['quorum']}"


def test_phase_a_threads_quorum_to_pipeline_builder():
    """_phase_a_build_cluster_cache(quorum=3) passes quorum to _build_ensemble_pipeline_for_phase_a."""
    import tests.benchmark_harness as harness_module

    captured_quorum: list = []

    def fake_builder(encoder_cfgs, quorum=None):
        captured_quorum.append(quorum)
        mock = MagicMock()
        mock.ainvoke = MagicMock(return_value={})
        return mock

    # Provide a minimal chunk dict so load_chunks returns non-empty and the
    # early-exit guard (if not medallion_chunks: return) doesn't fire.
    # Field names match TextChunk's Pydantic model (index, not chunk_index).
    fake_chunk = {
        "chunk_id": "test::chunk::0",
        "document_id": "test-doc",
        "text": "synthetic chunk for quorum threading test",
        "index": 0,
        "total_chunks": 1,
        "metadata": {"source": "test", "domain": "test"},
    }

    # Patch at the module level where _phase_a_build_cluster_cache looks up the name
    original = harness_module._build_ensemble_pipeline_for_phase_a
    harness_module._build_ensemble_pipeline_for_phase_a = fake_builder  # type: ignore[assignment]
    try:
        # May fail at doc processing — we only care the quorum was forwarded.
        with (
            patch.object(harness_module, "load_chunks", return_value=[fake_chunk]),
            contextlib.suppress(Exception),
        ):
            harness_module._phase_a_build_cluster_cache(sample_n=1, quorum=3)
    finally:
        harness_module._build_ensemble_pipeline_for_phase_a = original  # type: ignore[assignment]

    assert captured_quorum, "_build_ensemble_pipeline_for_phase_a was never called"
    assert captured_quorum[0] == 3, f"Expected quorum=3, got {captured_quorum[0]}"
