"""Shared executor factories for catalyst-data Dagster code locations.

Two modes:
- k8s: each step gets its own pod (for GPU/heavy workloads like media-ingest)
- in-process: all steps run in the run pod (for API/LLM-over-HTTP workloads)
"""

from __future__ import annotations

import os

from dagster import ExecutorDefinition, in_process_executor
from dagster_k8s import k8s_job_executor


def make_k8s_executor(code_location: str) -> ExecutorDefinition:
    """Build a k8s_job_executor that spawns a pod per step — falling back
    to ``in_process_executor`` when the process isn't running inside a
    k8s cluster.

    Use for code locations with GPU/heavy-compute steps (media-ingest).
    Env vars are hardcoded from the code-server's os.environ at import
    time.

    Host-mode detection: ``KUBERNETES_SERVICE_HOST`` is set by the
    kubelet inside every pod; ``task dev`` on a developer's Mac never
    sets it. When we can't see that env var, returning the in-process
    executor avoids the ``kubernetes.config.load_incluster_config``
    ConfigException ("Service host/port is not set") that
    ``K8sStepHandler.__init__`` raises during every run's setup —
    code-location import would succeed but every run would crash
    before the first step.
    """
    # In-cluster detection — fall back to in-process for host-mode dev.
    if not os.environ.get("KUBERNETES_SERVICE_HOST"):
        return in_process_executor

    # Build env list from code-server environment at import time.
    # These get injected into every step pod via container_config.env.
    env = [{"name": "DAGSTER_CODE_LOCATION", "value": code_location}]

    for var in [
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
        "CONGRESS_API_KEY",
        "OPENAI_API_KEY",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
        "LOG_LEVEL",
    ]:
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
        },
        name=f"k8s_job_executor_{code_location}",
    )


def make_in_process_executor(code_location: str) -> ExecutorDefinition:
    """In-process executor — all steps run sequentially in the run pod.

    The run pod gets env vars from dagster-instance.yaml run_k8s_config.
    No per-step pod overhead.
    """
    return in_process_executor
