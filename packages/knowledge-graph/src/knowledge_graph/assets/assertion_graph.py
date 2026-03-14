"""Platinum: Assertion graph — links assertions to canonical entities.

Reads gold-layer assertions from all source code locations,
links subject/object to canonical entity IDs, and writes to
PostgreSQL + Neo4j.
"""

from collections import defaultdict
from typing import Any

from dagster import AssetExecutionContext, Output, asset
from dagster_io import Assertion, CanonicalEntity

from knowledge_graph.resources import GraphDBResource


def _build_name_index(entities: list[CanonicalEntity]) -> dict[str, str]:
    """Build a case-insensitive name → canonical_id lookup.

    Indexes canonical_name + all aliases for each entity.
    """
    index: dict[str, str] = {}
    for ent in entities:
        key = ent.canonical_name.lower().strip()
        if key not in index:
            index[key] = ent.canonical_id
        for alias in ent.aliases:
            alias_key = alias.lower().strip()
            if alias_key not in index:
                index[alias_key] = ent.canonical_id
    return index


def _resolve_entity_id(text: str, name_index: dict[str, str]) -> str | None:
    """Try to resolve text to a canonical entity ID."""
    key = text.lower().strip()
    if key in name_index:
        return name_index[key]
    # Try substring match (text contained in an entity name)
    for name, cid in name_index.items():
        if key in name or name in key:
            return cid
    return None


@asset(
    group_name="knowledge_graph",
    description="Link assertions to canonical entities and write to graph stores (platinum layer)",
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
def assertion_graph(
    context: AssetExecutionContext,
    graph_db: GraphDBResource,
    canonical_entities: list[CanonicalEntity],
    congress_assertions: list[Assertion],
    leak_assertions: list[Assertion],
) -> Output[dict[str, Any]]:
    all_assertions = congress_assertions + leak_assertions
    context.log.info(
        f"Processing {len(all_assertions)} assertions against "
        f"{len(canonical_entities)} canonical entities"
    )

    # Build name index for entity resolution
    name_index = _build_name_index(canonical_entities)

    # Resolve assertion subjects/objects to canonical entity IDs
    linked: list[dict[str, Any]] = []
    unlinked_count = 0
    stats: dict[str, int] = defaultdict(int)

    for assertion in all_assertions:
        subj_id = _resolve_entity_id(assertion.subject_text, name_index)
        obj_id = _resolve_entity_id(assertion.object_text, name_index)

        record = assertion.model_dump()
        record["subject_canonical_id"] = subj_id
        record["object_canonical_id"] = obj_id
        if assertion.provenance:
            record["source_document_id"] = assertion.provenance.source_document_id
            record["chunk_id"] = assertion.provenance.chunk_id
            record["code_location"] = assertion.provenance.code_location

        linked.append(record)

        if subj_id and obj_id:
            stats["fully_linked"] += 1
        elif subj_id or obj_id:
            stats["partially_linked"] += 1
        else:
            unlinked_count += 1

    context.log.info(
        f"Linked: {stats['fully_linked']} full, {stats['partially_linked']} partial, "
        f"{unlinked_count} unlinked"
    )

    # Write to PostgreSQL
    pg_count = graph_db.upsert_assertions(linked)
    context.log.info(f"Wrote {pg_count} assertions to PostgreSQL")

    # Write fully-linked assertions to Neo4j as edges
    fully_linked = [a for a in linked if a.get("subject_canonical_id") and a.get("object_canonical_id")]
    neo4j_count = graph_db.sync_assertions_to_neo4j(fully_linked)
    context.log.info(f"Wrote {neo4j_count} assertion edges to Neo4j")

    result = {
        "total_assertions": len(all_assertions),
        "fully_linked": stats["fully_linked"],
        "partially_linked": stats["partially_linked"],
        "unlinked": unlinked_count,
        "pg_upserted": pg_count,
        "neo4j_synced": neo4j_count,
    }

    return Output(
        result,
        metadata=result,
    )
