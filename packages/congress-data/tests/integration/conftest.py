"""Fixtures for congress-data pipeline integration tests.

Runs real assets against a local filesystem IO manager, writing
human-readable JSON/JSONL so you can inspect intermediate results.

Usage:
    CONGRESS_API_KEY=xxx DAGSTER_CODE_LOCATION=congress_data \
        pytest packages/congress-data/tests/integration/ -v -s

    # Just bronze (no LLM):
    pytest packages/congress-data/tests/integration/ -v -s -k "bronze"

    # Full chain including LLM:
    LLM_BASE_URL=http://litellm.talos00:4000/v1 LLM_MODEL=runpod/qwen3:30b-a3b \
        pytest packages/congress-data/tests/integration/ -v -s -k "full_chain"
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from dagster import DagsterInstance
from tests.shared.local_io_manager import LocalJsonIOManager

from dagster_io import ChunkingResource

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tests" / "congress-pipeline-output"

os.environ.setdefault("DAGSTER_CODE_LOCATION", "congress_data")


def pytest_addoption(parser):
    parser.addoption(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for pipeline output",
    )
    parser.addoption(
        "--keep-output",
        action="store_true",
        default=True,
        help="Don't clean output directory before run",
    )
    parser.addoption(
        "--partition",
        default="119-hres-1",
        help="Bill partition key to test (default: 119-hres-1, a small resolution)",
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
        "append_io_manager": local_io_manager,
        "chunking": ChunkingResource(),
    }
    if os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        from dagster_io import EmbeddingResource, LLMResource

        resources["llm"] = LLMResource()
        resources["embeddings"] = EmbeddingResource()
    return resources


@pytest.fixture(scope="session")
def dagster_instance(output_dir) -> DagsterInstance:
    """Ephemeral Dagster instance for dynamic partition registration."""
    instance = DagsterInstance.ephemeral()
    return instance


@pytest.fixture(scope="session")
def partition_key(request, dagster_instance) -> str:
    """Register the test partition key in the ephemeral instance."""
    pk = request.config.getoption("--partition")
    dagster_instance.add_dynamic_partitions("congress_bill", [pk])
    dagster_instance.add_dynamic_partitions("congress_member", [pk])
    return pk


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: requires LLM API key")
    config.addinivalue_line("markers", "slow: long-running test")
