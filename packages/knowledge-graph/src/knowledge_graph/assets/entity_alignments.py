"""Platinum: Entity alignment edges for audit and inspection.

Materializes AlignmentEdge objects produced by the CrossSourceAligner,
writes to PostgreSQL + Neo4j for graph traversal.
"""

from dagster import AllPartitionMapping, AssetExecutionContext, AssetIn, AutomationCondition, Output, asset

import dagster_io.concordance as _concordance_mod
import knowledge_graph.resources as _resources_mod
from dagster_io import (
    AlignmentEdge,
    CrossSourceAligner,
    EntityCandidate,
)
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.versioning import code_version_from_modules
from knowledge_graph.assets.canonical_entities import _flatten_partition_fanin
from knowledge_graph.resources import GraphDBResource

_CODE_VERSION = code_version_from_modules(_concordance_mod, _resources_mod)

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="knowledge_graph",
    description="Cross-source entity alignment edges (platinum layer)",
    compute_kind="python",
    code_version=_CODE_VERSION,
    automation_condition=AutomationCondition.eager(),
    metadata={"layer": "platinum"},
    ins={
        "media_entity_candidates": AssetIn(
            partition_mapping=AllPartitionMapping(),
            input_manager_key="optional_io_manager",
        ),
        "congress_entity_candidates": AssetIn(input_manager_key="optional_io_manager"),
        "leak_entity_candidates": AssetIn(input_manager_key="optional_io_manager"),
    },
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
    media_entity_candidates,  # dict[str, list[EntityCandidate]] fan-in (or None)
    congress_entity_candidates: list[EntityCandidate] | None = None,
    leak_entity_candidates: list[EntityCandidate] | None = None,
) -> Output[list[AlignmentEdge]]:
    media_entity_candidates = _flatten_partition_fanin(media_entity_candidates, EntityCandidate)
    congress_entity_candidates = congress_entity_candidates or []
    leak_entity_candidates = leak_entity_candidates or []
    with trace_operation(
        "entity_alignments",
        tracer,
        {
            "code_location": "knowledge_graph",
            "layer": "platinum",
            "congress_candidate_count": len(congress_entity_candidates),
            "leak_candidate_count": len(leak_entity_candidates),
            "media_candidate_count": len(media_entity_candidates),
        },
    ):
        logger.info(
            "Starting entity_alignments: %d congress + %d leak + %d media candidates",
            len(congress_entity_candidates),
            len(leak_entity_candidates),
            len(media_entity_candidates),
        )
        context.log.info(
            f"Computing alignment edges: {len(congress_entity_candidates)} congress "
            f"+ {len(leak_entity_candidates)} leak "
            f"+ {len(media_entity_candidates)} media candidates"
        )

        aligner = CrossSourceAligner()
        sources = {
            "congress_data": congress_entity_candidates,
            "open_leaks": leak_entity_candidates,
            "media_ingest": media_entity_candidates,
        }

        # Phase 1: intra-source — collapse duplicates within each source
        intra_edges: list = []
        for loc, candidates in sources.items():
            if len(candidates) > 1:
                intra_edges.extend(aligner.intra_source_align(candidates, loc))
        context.log.info(f"Intra-source alignment: {len(intra_edges)} edges")

        # Phase 2: cross-source — pairwise between different sources
        cross_edges = aligner.align(sources)
        context.log.info(f"Cross-source alignment: {len(cross_edges)} edges")

        edges = intra_edges + cross_edges

        # Count by type
        same_as_count = sum(1 for e in edges if e.alignment_type.value == "sameAs")
        possible_count = sum(1 for e in edges if e.alignment_type.value == "possibleSameAs")
        ASSET_RECORDS_PROCESSED.labels(
            code_location="knowledge_graph",
            asset_key="entity_alignments",
            layer="platinum",
        ).inc(len(edges))
        logger.info(
            "entity_alignments complete: %d edges (%d sameAs, %d possibleSameAs)",
            len(edges),
            same_as_count,
            possible_count,
        )
        context.log.info(f"Found {len(edges)} alignment edges: {same_as_count} sameAs, {possible_count} possibleSameAs")

        # Enrich edges with human-readable metadata before writing.
        # Build candidate_id → candidate lookup for denormalization.
        all_candidates = congress_entity_candidates + leak_entity_candidates + media_entity_candidates
        cand_by_id = {c.candidate_id: c for c in all_candidates}

        edge_dicts = [e.model_dump() for e in edges]
        for d in edge_dicts:
            d["alignment_type"] = (
                d["alignment_type"].value if hasattr(d["alignment_type"], "value") else d["alignment_type"]
            )
            src = cand_by_id.get(d["source_entity_id"])
            tgt = cand_by_id.get(d["target_entity_id"])
            d["source_name"] = src.canonical_name if src else ""
            d["target_name"] = tgt.canonical_name if tgt else ""
            d["entity_type"] = (
                src.candidate_type.value
                if src and hasattr(src.candidate_type, "value")
                else (src.candidate_type if src else "")
            )
            d["source_code_location"] = src.code_location if src else ""
            d["target_code_location"] = tgt.code_location if tgt else ""

        pg_count = graph_db.upsert_alignment_edges(edge_dicts)
        context.log.info(f"Wrote {pg_count} edges to PostgreSQL")

        neo4j_count = graph_db.sync_alignment_edges_to_neo4j(edge_dicts)
        context.log.info(f"Wrote {neo4j_count} edges to Neo4j")

        return Output(
            edges,
            metadata={
                "edge_count": len(edges),
                "intra_source_edges": len(intra_edges),
                "cross_source_edges": len(cross_edges),
                "same_as_count": same_as_count,
                "possible_same_as_count": possible_count,
                "pg_upserted": pg_count,
                "neo4j_synced": neo4j_count,
            },
        )
