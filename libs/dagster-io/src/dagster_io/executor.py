"""Shared executor factories for catalyst-data Dagster code locations.

Two modes:
- k8s: each step gets its own pod (for GPU/heavy workloads like media-ingest)
- in-process: all steps run in the run pod (for API/LLM-over-HTTP workloads like congress-data)
"""

from __future__ import annotations

import os

from dagster import ExecutorDefinition, in_process_executor
from dagster_k8s import k8s_job_executor

# Env vars to propagate to step pods via explicit value injection.
_PROPAGATED_ENV_VARS = [
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_DIMENSIONS",
    "DAGSTER_S3_ENDPOINT_URL",
    "DAGSTER_S3_ACCESS_KEY",
    "DAGSTER_S3_SECRET_KEY",
    "DAGSTER_S3_BUCKET",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SERVICE_NAME",
    "LOG_LEVEL",
    "OPENAI_API_KEY",
]


def make_k8s_executor(code_location: str) -> ExecutorDefinition:
    """Build a k8s_job_executor that spawns a pod per step.

    Use for code locations with GPU/heavy-compute steps (media-ingest).
    Steps needing specific node types use op_tags for tolerations/resources.
    """
    env = [{"name": "DAGSTER_CODE_LOCATION", "value": code_location}]

    for var in _PROPAGATED_ENV_VARS:
        val = os.environ.get(var)
        if val is not None:
            env.append({"name": var, "value": val})

    return k8s_job_executor.configured(
        {
            "step_k8s_config": {
                "container_config": {
                    "env": env,
                },
            },
            "env_vars": [
                var
                for var in ["DAGSTER_CODE_LOCATION", *_PROPAGATED_ENV_VARS]
                if os.environ.get(var) is not None or var == "DAGSTER_CODE_LOCATION"
            ],
        },
        name=f"k8s_job_executor_{code_location}",
    )


def make_in_process_executor(code_location: str) -> ExecutorDefinition:
    """In-process executor — all steps run sequentially in the run pod.

    Use for code locations where all steps are API calls or LLM-over-HTTP
    (congress-data, open-leaks, knowledge-graph). No per-step pod overhead.
    The run pod already has all env vars from dagster-instance.yaml.
    """
    return in_process_executor
