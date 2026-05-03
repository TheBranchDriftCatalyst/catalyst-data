"""IO backend selector — pick between MinIO (prod) and local filesystem (dev/test).

Switched at runtime by the ``DAGSTER_IO_BACKEND`` env var:

- ``DAGSTER_IO_BACKEND=local``  → ``LocalJsonIOManager`` family writes to
  ``base_dir`` on disk. Used by integration tests (each conftest binds the
  managers explicitly) and ``task dev`` (each code-location's ``Definitions``
  picks the local set so a developer can run the entire pipeline against
  fixture data without S3).
- ``DAGSTER_IO_BACKEND=minio`` (default, or unset) → MinIO/S3 family for
  production deployments.

Each code-location's ``Definitions`` calls :func:`select_io_managers` once and
spreads only the keys it actually declares (``io_manager``, optionally
``optional_io_manager``, optionally ``append_io_manager``).
"""

from __future__ import annotations

import os
from typing import Any

from dagster import EnvVar


def select_io_managers(default_local_dir: str) -> dict[str, Any]:
    """Return ``{io_manager, optional_io_manager, append_io_manager}`` for the
    backend chosen by ``DAGSTER_IO_BACKEND``.

    The caller spreads only the keys its ``Definitions`` declares — extra keys
    are harmless because Dagster ignores resources not referenced by any asset.

    The S3-targeted managers receive their connection params via ``EnvVar(...)``
    so the Dagster launchpad UI surfaces them as overridable per-run config
    instead of baking the env-var values in at code-server import time.
    """
    backend = os.environ.get("DAGSTER_IO_BACKEND", "minio").lower()
    if backend == "local":
        from dagster_io.local_io_manager import LocalAppendIOManager, LocalJsonIOManager, LocalOptionalIOManager

        model_tag = os.environ.get("LLM_MODEL", "")
        return {
            "io_manager": LocalJsonIOManager(base_dir=default_local_dir, model_tag=model_tag),
            "optional_io_manager": LocalOptionalIOManager(base_dir=default_local_dir, model_tag=model_tag),
            "append_io_manager": LocalAppendIOManager(base_dir=default_local_dir),
        }

    from dagster_io.append_io_manager import AppendIOManager
    from dagster_io.io_manager import MinioIOManager, OptionalMinioIOManager

    s3_env = {
        "endpoint_url": EnvVar("DAGSTER_S3_ENDPOINT_URL"),
        "access_key": EnvVar("DAGSTER_S3_ACCESS_KEY"),
        "secret_key": EnvVar("DAGSTER_S3_SECRET_KEY"),
        "bucket": EnvVar("DAGSTER_S3_BUCKET"),
    }
    return {
        "io_manager": MinioIOManager(**s3_env),
        "optional_io_manager": OptionalMinioIOManager(**s3_env),
        "append_io_manager": AppendIOManager(**s3_env),
    }
