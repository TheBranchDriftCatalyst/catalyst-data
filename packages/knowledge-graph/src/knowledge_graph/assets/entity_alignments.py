"""Platinum: Entity alignment edges for audit and inspection.

Materializes AlignmentEdge objects produced by the CrossSourceAligner,
writes to PostgreSQL + Neo4j for graph traversal.
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import (
    AlignmentEdge,
    CrossSourceAligner,
    EntityCandidate,
)

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from knowledge_graph.resources import GraphDBResource

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="knowledge_graph",
    description="Cross-source entity alignment edges (platinum layer)",
    compute_kind="python",
    metadata={"layer": "platinum"},
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
def entity_alignments(
    context: AssetExecutionContext,
    graph_db: GraphDBResource,
    congress_entity_candidates: list[EntityCandidate],
    leak_entity_candidates: list[EntityCandidate],
) -> Output[list[AlignmentEdge]]:
    with trace_operation("entity_alignments", tracer, {"code_location": "knowledge_graph", "layer": "platinum", "congress_candidate_count": len(congress_entity_candidates), "leak_candidate_count": len(leak_entity_candidates)}):
        logger.info("Starting entity_alignments: %d congress + %d leak candidates", len(congress_entity_candidates), len(leak_entity_candidates))
        context.log.info(
            f"Computing alignment edges: {len(congress_entity_candidates)} congress "
            f"+ {len(leak_entity_candidates)} leak candidates"
        )

        aligner = CrossSourceAligner()
        edges = aligner.align({
            "congress_data": congress_entity_candidates,
            "open_leaks": leak_entity_candidates,
        })

        # Count by type
        same_as_count = sum(1 for e in edges if e.alignment_type.value == "sameAs")
        possible_count = sum(1 for e in edges if e.alignment_type.value == "possibleSameAs")
        ASSET_RECORDS_PROCESSED.labels(code_location="knowledge_graph", asset_key="entity_alignments", layer="platinum").inc(len(edges))
        logger.info("entity_alignments complete: %d edges (%d sameAs, %d possibleSameAs)", len(edges), same_as_count, possible_count)
        context.log.info(
            f"Found {len(edges)} alignment edges: {same_as_count} sameAs, {possible_count} possibleSameAs"
        )

        # Write to PostgreSQL + Neo4j
        edge_dicts = [e.model_dump() for e in edges]
        for d in edge_dicts:
            d["alignment_type"] = d["alignment_type"].value if hasattr(d["alignment_type"], "value") else d["alignment_type"]

        pg_count = graph_db.upsert_alignment_edges(edge_dicts)
        context.log.info(f"Wrote {pg_count} edges to PostgreSQL")

        neo4j_count = graph_db.sync_alignment_edges_to_neo4j(edge_dicts)
        context.log.info(f"Wrote {neo4j_count} edges to Neo4j")

        return Output(
            edges,
            metadata={
                "edge_count": len(edges),
                "same_as_count": same_as_count,
                "possible_same_as_count": possible_count,
                "pg_upserted": pg_count,
                "neo4j_synced": neo4j_count,
            },
        )
