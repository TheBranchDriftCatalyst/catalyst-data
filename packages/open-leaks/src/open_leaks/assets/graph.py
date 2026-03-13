"""Gold: Knowledge graph construction from entities, propositions, and ICIJ relationships."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from open_leaks.entities import OffshoreRelationship


@asset(
    group_name="leaks",
    description="Build knowledge graph from leak entities, propositions, and ICIJ relationships",
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
def leak_graph(
    context: AssetExecutionContext,
    leak_entities: list[dict[str, Any]],
    icij_offshore_relationships: list[OffshoreRelationship],
) -> Output[dict[str, Any]]:
    raise NotImplementedError(
        "Graph loading requires Neo4j or similar graph DB — "
        "configure via NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD env vars. "
        "ICIJ relationships feed directly as edges; leak_entities provide nodes."
    )
