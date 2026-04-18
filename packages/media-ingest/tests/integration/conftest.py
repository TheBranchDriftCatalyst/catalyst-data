"""Fixtures for local Dagster pipeline integration tests.

Runs the real pipeline assets against a local filesystem IO manager,
writing human-readable JSON/JSONL to a persistent output directory
so you can inspect intermediate results.

Usage:
    pytest tests/integration/ -v
    pytest tests/integration/ -v -k "test_discovery"
    pytest tests/integration/ -v --output-dir /tmp/my-pipeline-run
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.shared.local_io_manager import LocalJsonIOManager

from dagster_io import ChunkingResource

# ── Path constants ─────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[4]  # catalyst-data/
DEMO_VIDEO = REPO_ROOT / "tests" / "demo_video.mp4"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tests" / "pipeline-output"


def pytest_addoption(parser):
    parser.addoption(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for pipeline output (default: tests/pipeline-output/)",
    )
    parser.addoption(
        "--keep-output",
        action="store_true",
        default=True,
        help="Don't clean output directory before run (default: keep)",
    )


# ── Core fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def output_dir(request) -> Path:
    """Persistent output directory for pipeline intermediates."""
    out = Path(request.config.getoption("--output-dir"))
    if not request.config.getoption("--keep-output") and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture(scope="session")
def local_io_manager(output_dir) -> LocalJsonIOManager:
    """Filesystem IO manager that writes JSON/JSONL to output_dir."""
    return LocalJsonIOManager(base_dir=str(output_dir))


@pytest.fixture(scope="session")
def test_resources(local_io_manager) -> dict:
    """Full resource dict for local pipeline execution.

    Swaps MinioIOManager → LocalJsonIOManager.
    LLM and embedding resources use env vars if available, otherwise skip.
    """
    resources = {
        "io_manager": local_io_manager,
        "optional_io_manager": local_io_manager,
        "chunking": ChunkingResource(),
    }

    # Only include LLM/embedding resources if API keys are available
    if os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        from dagster_io import EmbeddingResource, LLMResource

        resources["llm"] = LLMResource()
        resources["embeddings"] = EmbeddingResource()

    return resources


@pytest.fixture(scope="session")
def demo_video_path() -> Path:
    """Path to the test demo video."""
    if not DEMO_VIDEO.exists():
        pytest.skip(f"Demo video not found at {DEMO_VIDEO}")
    return DEMO_VIDEO


@pytest.fixture(scope="session")
def media_dir(output_dir, demo_video_path) -> Path:
    """Local media directory with the demo video (simulates NFS mount)."""
    media = output_dir / "media"
    media.mkdir(parents=True, exist_ok=True)
    dest = media / demo_video_path.name
    if not dest.exists():
        shutil.copy2(demo_video_path, dest)
    return media


# ── Markers ────────────────────────────────────────────────────────────────


def pytest_configure(config):
    config.addinivalue_line("markers", "gpu: requires GPU (transcription, diarization)")
    config.addinivalue_line("markers", "llm: requires LLM API key")
    config.addinivalue_line("markers", "slow: long-running test")
