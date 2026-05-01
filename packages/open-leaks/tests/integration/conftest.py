"""Fixtures for open-leaks pipeline integration tests.

Mirrors the congress-data and media-ingest patterns: real assets against a
local filesystem IO manager, all outputs to ``.test-output/open-leaks/``.

Usage:
    DAGSTER_CODE_LOCATION=open_leaks pytest packages/open-leaks/tests/integration/ -v -s
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

import pytest
from dagster import DagsterInstance

from dagster_io import ChunkingResource, LocalJsonIOManager

REPO_ROOT = Path(__file__).resolve().parents[4]
TEST_OUTPUT_ROOT = Path(os.environ.get("TEST_OUTPUT_ROOT", str(REPO_ROOT / ".test-output")))
DEFAULT_OUTPUT_DIR = TEST_OUTPUT_ROOT / "open-leaks"

os.environ.setdefault("DAGSTER_CODE_LOCATION", "open_leaks")


def _safe_addoption(parser, *args, **kwargs):
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
    return {
        "io_manager": local_io_manager,
        "chunking": ChunkingResource(),
    }


@pytest.fixture(scope="session")
def dagster_instance() -> DagsterInstance:
    return DagsterInstance.ephemeral()


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running test")
