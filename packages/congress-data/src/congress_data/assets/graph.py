"""Stage 6: Knowledge graph loading into Neo4j.

Stubbed — requires Neo4j instance and mention/assertion extraction output.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io import Mention
from dagster_io.logging import get_logger
from dagster_io.observability import get_tracer, trace_operation

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="congress",
    description="Load Congress mentions and relationships into Neo4j knowledge graph",
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
    bill_mentions: list[Mention],
) -> Output[dict[str, Any]]:
    with trace_operation(
        "congress_graph",
        tracer,
        {
            "code_location": "congress_data",
            "layer": "gold",
            "mention_count": len(bill_mentions),
        },
    ):
        raise NotImplementedError(
            "Graph loading requires Neo4j — configure via NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD env vars"
        )
