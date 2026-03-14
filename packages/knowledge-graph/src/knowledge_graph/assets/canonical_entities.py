"""Platinum: Cross-source canonical entity resolution.

Reads gold-layer EntityCandidates from all code locations,
runs CrossSourceAligner, and produces CanonicalEntity objects.
Dual-writes to PostgreSQL + Neo4j.
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import (
    CanonicalEntity,
    CrossSourceAligner,
    EntityCandidate,
)

from knowledge_graph.resources import GraphDBResource


@asset(
    group_name="knowledge_graph",
    description="Cross-source canonical entity resolution (platinum layer)",
    compute_kind="python",
    metadata={"layer": "platinum"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "500m", "memory": "2Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi"},
                }
            }
        }
    },
)
def canonical_entities(
    context: AssetExecutionContext,
    graph_db: GraphDBResource,
    congress_entity_candidates: list[EntityCandidate],
    leak_entity_candidates: list[EntityCandidate],
) -> Output[list[CanonicalEntity]]:
    context.log.info(
        f"Resolving canonical entities from {len(congress_entity_candidates)} congress "
        f"+ {len(leak_entity_candidates)} leak candidates"
    )

    # Run cross-source alignment
    aligner = CrossSourceAligner()
    sources = {
        "congress_data": congress_entity_candidates,
        "open_leaks": leak_entity_candidates,
    }
    alignment_edges = aligner.align(sources)
    context.log.info(f"Found {len(alignment_edges)} cross-source alignment edges")

    # Build canonical entities from all candidates
    all_candidates = congress_entity_candidates + leak_entity_candidates
    canonical_list: list[CanonicalEntity] = []

    # Build alignment groups (union-find on sameAs edges)
    from dagster_io.concordance import _UnionFind

    uf = _UnionFind()
    for cand in all_candidates:
        uf.find(cand.candidate_id)
    for edge in alignment_edges:
        if edge.alignment_type.value == "sameAs":
            uf.union(edge.source_entity_id, edge.target_entity_id)

    clusters = uf.clusters()
    cand_by_id = {c.candidate_id: c for c in all_candidates}

    for _root, member_ids in clusters.items():
        members = [cand_by_id[mid] for mid in member_ids if mid in cand_by_id]
        if not members:
            continue

        # Pick canonical name from highest mention_count member
        primary = max(members, key=lambda c: c.mention_count)

        all_aliases: set[str] = set()
        all_code_locations: set[str] = set()
        total_mentions = 0
        for m in members:
            all_aliases.add(m.canonical_name)
            all_aliases.update(m.aliases)
            all_code_locations.add(m.code_location)
            total_mentions += m.mention_count

        all_aliases.discard(primary.canonical_name)

        canonical = CanonicalEntity(
            canonical_name=primary.canonical_name,
            entity_type=primary.candidate_type,
            aliases=sorted(all_aliases),
            source_candidate_ids=[m.candidate_id for m in members],
            source_code_locations=sorted(all_code_locations),
            embedding=primary.embedding,
            mention_count=total_mentions,
        )
        canonical_list.append(canonical)

    context.log.info(f"Produced {len(canonical_list)} canonical entities")

    # Dual-write to PostgreSQL + Neo4j
    entity_dicts = [e.model_dump() for e in canonical_list]
    for d in entity_dicts:
        d["entity_type"] = d["entity_type"].value if hasattr(d["entity_type"], "value") else d["entity_type"]

    pg_count = graph_db.upsert_canonical_entities(entity_dicts)
    context.log.info(f"Wrote {pg_count} entities to PostgreSQL")

    neo4j_count = graph_db.sync_entities_to_neo4j(entity_dicts)
    context.log.info(f"Wrote {neo4j_count} entities to Neo4j")

    return Output(
        canonical_list,
        metadata={
            "canonical_entity_count": len(canonical_list),
            "source_candidates": len(all_candidates),
            "alignment_edges": len(alignment_edges),
            "pg_upserted": pg_count,
            "neo4j_synced": neo4j_count,
        },
    )
