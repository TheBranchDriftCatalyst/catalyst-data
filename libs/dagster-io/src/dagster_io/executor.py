"""Shared executor factories for catalyst-data Dagster code locations.

Every code location in the monorepo uses ``k8s_job_executor`` with the same
shape: propagate ``DAGSTER_CODE_LOCATION`` to every step pod so that
``dagster_io.path_builder`` can build S3 paths correctly.

Prior to this helper the config was copy-pasted into each
``packages/*/src/*/__init__.py``. Using a single factory keeps the 4 code
locations in sync and gives one place to evolve the k8s config as our
requirements grow (e.g. adding more env vars, tweaking pod spec).
"""

from __future__ import annotations

import os

from dagster import ExecutorDefinition
from dagster_k8s import k8s_job_executor

# Env vars to propagate from code-server → step pods (beyond DAGSTER_CODE_LOCATION).
# These are read from the code-server's os.environ at import time and injected into
# every step pod the executor spawns.
_PROPAGATE_ENV_VARS = [
    "SPEAKER_PROFILE_ENABLED",
]


def make_k8s_executor(code_location: str) -> ExecutorDefinition:
    """Build a ``k8s_job_executor`` that hardcodes ``DAGSTER_CODE_LOCATION``
    on every step pod it spawns.

    Args:
        code_location: The Dagster code location name (e.g. ``"media_ingest"``,
            ``"knowledge_graph"``). Must match the name registered in
            ``k8s/platform/workspace.yaml`` and the value of
            ``DAGSTER_CODE_LOCATION`` on the code-server deployment.

    Why this exists:
        ``dagster_io.path_builder._code_location_from_context()`` reads
        ``DAGSTER_CODE_LOCATION`` from the process environment to build S3
        keys like ``gold/<code_location>/<group>/<asset>/<partition>``. The
        code-server pod has this env var set via its k8s deployment manifest,
        but step pods spawned by ``k8s_job_executor`` do NOT inherit env vars
        from the code-server automatically.

        The naive form ``k8s_job_executor.configured({"env_vars":
        ["DAGSTER_CODE_LOCATION"]})`` looks like it would work but doesn't:
        ``env_vars`` reads from the **run pod's** ``os.environ`` at executor
        init time, and the run pod (spawned by ``K8sRunLauncher``) only
        inherits the instance-wide env block from
        ``k8s/platform/dagster-instance.yaml`` which is not per-code-location.

        The only reliable way is to hardcode the value in the executor's
        ``step_k8s_config.container_config.env`` list, which is then applied
        to every step pod the executor spawns. That's what this helper does.

    Returns:
        A configured ``ExecutorDefinition`` ready to pass as
        ``Definitions(executor=...)``.
    """
    env = [{"name": "DAGSTER_CODE_LOCATION", "value": code_location}]
    # Propagate optional feature flags from the code-server environment
    for var in _PROPAGATE_ENV_VARS:
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
