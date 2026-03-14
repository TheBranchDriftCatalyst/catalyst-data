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

from knowledge_graph.resources import GraphDBResource


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
