"""Fixtures for media-ingest pipeline integration tests.

Runs real assets against a local filesystem IO manager.
All outputs go to TEST_OUTPUT_ROOT/media-ingest/ (default: .test-output/media-ingest/).

Usage:
    DAGSTER_CODE_LOCATION=media_ingest pytest packages/media-ingest/tests/integration/ -v -s
    pytest ... --output-dir /tmp/my-run
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.shared.local_io_manager import LocalJsonIOManager

from dagster_io import ChunkingResource

REPO_ROOT = Path(__file__).resolve().parents[4]
TEST_OUTPUT_ROOT = Path(os.environ.get("TEST_OUTPUT_ROOT", str(REPO_ROOT / ".test-output")))
DEFAULT_OUTPUT_DIR = TEST_OUTPUT_ROOT / "media-ingest"
DEMO_VIDEO = REPO_ROOT / "tests" / "demo_video.mp4"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"


def pytest_addoption(parser):
    parser.addoption(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory for pipeline output (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.addoption(
        "--keep-output",
        action="store_true",
        default=True,
        help="Don't clean output directory before run (default: keep)",
    )


@pytest.fixture(scope="session")
def output_dir(request) -> Path:
    out = Path(request.config.getoption("--output-dir"))
    if not request.config.getoption("--keep-output") and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture(scope="session")
def local_io_manager(output_dir) -> LocalJsonIOManager:
    return LocalJsonIOManager(base_dir=str(output_dir))


@pytest.fixture(scope="session")
def test_resources(local_io_manager) -> dict:
    resources = {
        "io_manager": local_io_manager,
        "optional_io_manager": local_io_manager,
        "chunking": ChunkingResource(),
    }
    if os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        from dagster_io import EmbeddingResource, LLMResource

        resources["llm"] = LLMResource()
        resources["embeddings"] = EmbeddingResource()
    return resources


@pytest.fixture(scope="session")
def demo_video_path() -> Path:
    if not DEMO_VIDEO.exists():
        pytest.skip(f"Demo video not found at {DEMO_VIDEO}")
    return DEMO_VIDEO


@pytest.fixture(scope="session")
def media_dir(output_dir, demo_video_path) -> Path:
    media = output_dir / "media"
    media.mkdir(parents=True, exist_ok=True)
    dest = media / demo_video_path.name
    if not dest.exists():
        shutil.copy2(demo_video_path, dest)
    return media


def pytest_configure(config):
    config.addinivalue_line("markers", "gpu: requires GPU (transcription, diarization)")
    config.addinivalue_line("markers", "llm: requires LLM API key")
    config.addinivalue_line("markers", "slow: long-running test")
