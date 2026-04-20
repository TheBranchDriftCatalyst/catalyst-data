"""Shared asset config for LLM extraction pipelines.

Provides:
- ``LLM_ASSET_K8S_CONFIG``: Standard k8s resource config for LLM assets.
"""

from __future__ import annotations

from dagster_io.logging import get_logger

logger = get_logger(__name__)

# ── Shared k8s config ────────────────────────────────────────────────

LLM_ASSET_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "resources": {
                "requests": {"cpu": "500m", "memory": "2Gi"},
                "limits": {"cpu": "2", "memory": "4Gi"},
            }
        }
    }
}
