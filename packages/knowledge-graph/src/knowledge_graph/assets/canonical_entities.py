"""Platinum: Cross-source canonical entity resolution.

Reads gold-layer EntityCandidates from all code locations,
runs CrossSourceAligner, and produces CanonicalEntity objects.
Dual-writes to PostgreSQL + Neo4j.
"""

from dagster import AllPartitionMapping, AssetExecutionContext, AssetIn, Output, asset

from dagster_io import (
    CanonicalEntity,
    CrossSourceAligner,
    EntityCandidate,
)


def _flatten_partition_fanin(value, model_cls=None) -> list:
    """Flatten a partition fan-in dict into a flat list.

    The MinioIOManager returns a ``dict[partition_key, value]`` when an
    unpartitioned asset consumes all partitions of an upstream partitioned
    asset via ``AllPartitionMapping``. We flatten that into a single list.
    Passes through None / list / single-value inputs unchanged.

    If ``model_cls`` is provided, any dict entries are coerced to instances
    of that pydantic model via ``model_cls(**d)``. The io manager's
    deserializer returns plain dicts when it can't resolve a concrete type
    hint (e.g. fan-in inputs whose function annotation is just a comment),
    so downstream code that expects model instances needs explicit
    reconstruction.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        flat: list = []
        for part in value.values():
            if part is None:
                continue
            if isinstance(part, list):
                flat.extend(part)
            else:
                flat.append(part)
    elif isinstance(value, list):
        flat = value
    else:
        flat = [value]

    if model_cls is not None:
        flat = [model_cls(**item) if isinstance(item, dict) else item for item in flat]
    return flat


from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED, CANONICAL_ENTITIES_TOTAL
from dagster_io.observability import get_tracer, trace_operation
from knowledge_graph.resources import GraphDBResource

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="knowledge_graph",
    description="Cross-source canonical entity resolution (platinum layer)",
    compute_kind="python",
    metadata={"layer": "platinum"},
    ins={
        # media_entity_candidates is partitioned by document_id in media_ingest;
        # we fan in all partitions into an unpartitioned view here. Individual
        # missing partitions are tolerated by OptionalMinioIOManager.
        "media_entity_candidates": AssetIn(
            partition_mapping=AllPartitionMapping(),
            input_manager_key="optional_io_manager",
        ),
        # Optional cross-source inputs — if the source code location has
        # never materialized these, the OptionalMinioIOManager returns None
        # and the asset body falls back to an empty list.
        "congress_entity_candidates": AssetIn(input_manager_key="optional_io_manager"),
        "leak_entity_candidates": AssetIn(input_manager_key="optional_io_manager"),
    },
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
    media_entity_candidates,  # dict[str, list[EntityCandidate]] fan-in (or None)
    congress_entity_candidates: list[EntityCandidate] | None = None,
    leak_entity_candidates: list[EntityCandidate] | None = None,
) -> Output[list[CanonicalEntity]]:
    media_entity_candidates = _flatten_partition_fanin(media_entity_candidates, EntityCandidate)
    congress_entity_candidates = congress_entity_candidates or []
    leak_entity_candidates = leak_entity_candidates or []
    with trace_operation(
        "canonical_entities",
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
            "Starting canonical_entities resolution: %d congress + %d leak + %d media candidates",
            len(congress_entity_candidates),
            len(leak_entity_candidates),
            len(media_entity_candidates),
        )
        context.log.info(
            f"Resolving canonical entities from {len(congress_entity_candidates)} congress "
            f"+ {len(leak_entity_candidates)} leak "
            f"+ {len(media_entity_candidates)} media candidates"
        )

        # Run intra-source alignment first — collapse duplicates that
        # ConcordanceEngine.resolve() couldn't merge across partitions.
        # Then run cross-source alignment on the surviving candidates.
        aligner = CrossSourceAligner()
        sources = {
            "congress_data": congress_entity_candidates,
            "open_leaks": leak_entity_candidates,
            "media_ingest": media_entity_candidates,
        }

        # Phase 1: intra-source — same _score_pair logic within each source
        intra_edges: list = []
        for loc, candidates in sources.items():
            if len(candidates) > 1:
                intra_edges.extend(aligner.intra_source_align(candidates, loc))
        context.log.info(f"Intra-source alignment: {len(intra_edges)} edges")

        # Phase 2: cross-source — pairwise between different sources
        cross_edges = aligner.align(sources)
        context.log.info(f"Cross-source alignment: {len(cross_edges)} edges")

        alignment_edges = intra_edges + cross_edges
        context.log.info(f"Total alignment edges: {len(alignment_edges)}")

        # Build canonical entities from all candidates
        all_candidates = congress_entity_candidates + leak_entity_candidates + media_entity_candidates
        canonical_list: list[CanonicalEntity] = []

        # Build alignment groups (union-find on sameAs edges)
        from dagster_io.concordance import _UnionFind

        uf = _UnionFind()
        for cand in all_candidates:
            uf.find(cand.candidate_id)
        for edge in alignment_edges:
            if edge.alignment_type.value == "sameAs":
                uf.union(edge.source_entity_id, edge.target_entity_id)

        MAX_CLUSTER_SIZE = 20
        raw_clusters = uf.clusters()

        # --- Cluster-size safety cap ---
        # If transitive closure produces a mega-cluster (> MAX_CLUSTER_SIZE
        # members), split it by removing the weakest edge links.  We keep only
        # the top-N members by mention_count; the rest become singletons.
        # This prevents catastrophic over-merge regardless of scoring quality.
        clusters: dict[str, list[str]] = {}
        cand_by_id = {c.candidate_id: c for c in all_candidates}
        for root, member_ids in raw_clusters.items():
            if len(member_ids) <= MAX_CLUSTER_SIZE:
                clusters[root] = member_ids
            else:
                logger.warning(
                    "Oversized cluster (%d members) rooted at %s — capping at %d. Sample members: %s",
                    len(member_ids),
                    root,
                    MAX_CLUSTER_SIZE,
                    [cand_by_id[mid].canonical_name for mid in member_ids[:5] if mid in cand_by_id],
                )
                # Keep the top-N by mention_count; eject the rest as singletons
                scored = sorted(
                    member_ids,
                    key=lambda mid: cand_by_id[mid].mention_count if mid in cand_by_id else 0,
                    reverse=True,
                )
                clusters[root] = scored[:MAX_CLUSTER_SIZE]
                for singleton_id in scored[MAX_CLUSTER_SIZE:]:
                    clusters[singleton_id] = [singleton_id]

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

        ASSET_RECORDS_PROCESSED.labels(
            code_location="knowledge_graph",
            asset_key="canonical_entities",
            layer="platinum",
        ).inc(len(canonical_list))

        # Per-canonical observability: bucket by cross-source merge count so
        # Grafana can distinguish singletons (source_count_bucket="1" — the
        # aligner found no cross-source match, platinum is a pass-through)
        # from actual cross-source merges ("2", "3+").
        for canonical in canonical_list:
            source_count = len(canonical.source_code_locations or [])
            if source_count <= 1:
                bucket = "1"
            elif source_count == 2:
                bucket = "2"
            else:
                bucket = "3+"
            entity_type = (
                canonical.entity_type.value if hasattr(canonical.entity_type, "value") else str(canonical.entity_type)
            )
            CANONICAL_ENTITIES_TOTAL.labels(
                entity_type=entity_type,
                source_count_bucket=bucket,
            ).inc()

        logger.info(
            "canonical_entities complete: %d canonical entities from %d candidates",
            len(canonical_list),
            len(all_candidates),
        )
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
