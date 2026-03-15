"""Stage 6: Knowledge graph loading into Neo4j.

Stubbed — requires Neo4j instance and entity extraction output.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io.logging import get_logger

logger = get_logger(__name__)


@asset(
    group_name="congress",
    description="Load Congress entities and relationships into Neo4j knowledge graph",
    compute_kind="graph",
    metadata={"layer": "gold"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "250m", "memory": "1Gi"},
                    "limits": {"cpu": "1", "memory": "2Gi"},
                }
            }
        }
    },
)
def congress_graph(
    context: AssetExecutionContext,
    congress_entities: list[dict[str, Any]],
) -> Output[dict[str, Any]]:
    raise NotImplementedError(
        "Graph loading requires Neo4j — configure via NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD env vars"
    )
