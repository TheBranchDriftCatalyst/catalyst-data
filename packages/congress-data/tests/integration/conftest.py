"""Fixtures for congress-data pipeline integration tests.

All outputs go to TEST_OUTPUT_ROOT/congress-data/ (default: .test-output/congress-data/).

Usage:
    CONGRESS_API_KEY=xxx pytest packages/congress-data/tests/integration/ -v -s
    pytest ... -k "bronze"                    # API only, no LLM
    pytest ... --partition 119-hr-1           # test a bigger bill
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

import pytest
from dagster import DagsterInstance

from dagster_io import ChunkingResource, LocalJsonIOManager

REPO_ROOT = (
    Path(__file__).resolve().parents[4]
)  # TODO: this pattern is gonna be all over perhaps we should centralize it somewhere or create a ENV var
TEST_OUTPUT_ROOT = Path(os.environ.get("TEST_OUTPUT_ROOT", str(REPO_ROOT / ".test-output")))
DEFAULT_OUTPUT_DIR = TEST_OUTPUT_ROOT / "congress-data"

os.environ.setdefault("DAGSTER_CODE_LOCATION", "congress_data")


def _safe_addoption(parser, *args, **kwargs):
    """Add a pytest option, silently skipping if already registered."""
    with contextlib.suppress(ValueError):
        parser.addoption(*args, **kwargs)


def pytest_addoption(parser):
    _safe_addoption(
        parser,
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory for pipeline output (default: {DEFAULT_OUTPUT_DIR})",
    )
    _safe_addoption(
        parser,
        "--keep-output",
        action="store_true",
        default=True,
        help="Don't clean output directory before run",
    )
    _safe_addoption(
        parser,
        "--partition",
        default="119-hres-1",
        help="Bill partition key to test (default: 119-hres-1)",
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
    model_tag = os.environ.get("LLM_MODEL", "")
    return LocalJsonIOManager(base_dir=str(output_dir), model_tag=model_tag)


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
        # SemanticChunkingSeed embedder — separate registration (CD-wnu5).
        resources["embedding_seed"] = EmbeddingResource()
    return resources


@pytest.fixture(scope="session")
def dagster_instance(output_dir) -> DagsterInstance:
    return DagsterInstance.ephemeral()


@pytest.fixture(scope="session")
def partition_key(request, dagster_instance) -> str:
    pk = request.config.getoption("--partition")
    dagster_instance.add_dynamic_partitions("congress_bill", [pk])
    dagster_instance.add_dynamic_partitions("congress_member", [pk])
    return pk


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: requires LLM API key")
    config.addinivalue_line("markers", "slow: long-running test")
