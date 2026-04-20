"""Gold: Knowledge graph construction from mentions, assertions, and ICIJ relationships."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io import Mention
from dagster_io.logging import get_logger
from dagster_io.observability import get_tracer, trace_operation
from open_leaks.entities import OffshoreRelationship

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="leaks",
    description="Build knowledge graph from leak mentions, assertions, and ICIJ relationships",
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
    leak_mentions: list[Mention],
    icij_offshore_relationships: list[OffshoreRelationship],
) -> Output[dict[str, Any]]:
    with trace_operation(
        "leak_graph",
        tracer,
        {
            "code_location": "open_leaks",
            "layer": "gold",
            "mention_count": len(leak_mentions),
            "relationship_count": len(icij_offshore_relationships),
        },
    ):
        raise NotImplementedError(
            "Graph loading requires Neo4j or similar graph DB — "
            "configure via NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD env vars. "
            "ICIJ relationships feed directly as edges; leak_mentions provide nodes."
        )
