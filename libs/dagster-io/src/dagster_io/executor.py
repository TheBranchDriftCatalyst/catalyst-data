"""Shared executor factories for catalyst-data Dagster code locations.

Every code location in the monorepo uses ``k8s_job_executor`` with the same
shape: propagate critical env vars to every step pod so that path building,
LLM calls, and embedding calls work correctly.

Prior to this helper the config was copy-pasted into each
``packages/*/src/*/__init__.py``. Using a single factory keeps the 4 code
locations in sync and gives one place to evolve the k8s config as our
requirements grow.
"""

from __future__ import annotations

import os

from dagster import ExecutorDefinition
from dagster_k8s import k8s_job_executor

# Env vars that MUST be propagated from the code-server to every step pod.
# Step pods do NOT inherit env vars from the code-server deployment —
# they only get what's explicitly passed via step_k8s_config.
_PROPAGATED_ENV_VARS = [
    "DAGSTER_CODE_LOCATION",
    # LLM / Embedding config — without these, step pods fall back to
    # gpt-4o-mini / text-embedding-3-small defaults instead of the
    # configured model (e.g. runpod/qwen3:30b-a3b).
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_DIMENSIONS",
    # S3 credentials
    "DAGSTER_S3_ENDPOINT_URL",
    "DAGSTER_S3_ACCESS_KEY",
    "DAGSTER_S3_SECRET_KEY",
    "DAGSTER_S3_BUCKET",
    # Observability
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SERVICE_NAME",
    "LOG_LEVEL",
]


def make_k8s_executor(code_location: str) -> ExecutorDefinition:
    """Build a ``k8s_job_executor`` that propagates critical env vars
    to every step pod it spawns.

    Reads each var from ``os.environ`` at import time (code-server startup)
    and hardcodes them into the step pod env. Vars not set in the
    code-server environment are skipped.
    """
    env = [{"name": "DAGSTER_CODE_LOCATION", "value": code_location}]

    for var in _PROPAGATED_ENV_VARS:
        if var == "DAGSTER_CODE_LOCATION":
            continue  # already set above
        val = os.environ.get(var)
        if val:
            env.append({"name": var, "value": val})

    return k8s_job_executor.configured(
        {
            "step_k8s_config": {
                "container_config": {
                    "env": env,
                },
            },
        },
        name=f"k8s_job_executor_{code_location}",
    )
