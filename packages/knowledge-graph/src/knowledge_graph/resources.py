"""Graph database resources — Neo4j + PostgreSQL dual-write."""

import json
import os
from typing import Any

from dagster import ConfigurableResource

from dagster_io.logging import get_logger
from dagster_io.metrics import (
    GRAPH_DB_OPERATION_DURATION,
    GRAPH_DB_OPERATIONS,
    track_duration,
)

logger = get_logger(__name__)


# Runtime schema migrations for canonical_entities. Each entry is a
# standalone ``ALTER TABLE ... IF NOT EXISTS`` statement so it's safe to
# re-run on every resource use. The authoritative schema lives in the
# ``postgres-knowledge-init`` ConfigMap
# (``k8s/platform/postgres-knowledge.yaml``); those definitions only apply
# to fresh Postgres PVCs. Existing deployments whose init.sql ran before a
# column was added pick it up here on next upsert without requiring a
# manual migration.
_CANONICAL_ENTITIES_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE canonical_entities ADD COLUMN IF NOT EXISTS source_code_locations TEXT[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE canonical_entities ADD COLUMN IF NOT EXISTS source_candidate_ids TEXT[] DEFAULT '{}'",
)

_ALIGNMENT_EDGES_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE alignment_edges ADD COLUMN IF NOT EXISTS source_name TEXT DEFAULT ''",
    "ALTER TABLE alignment_edges ADD COLUMN IF NOT EXISTS target_name TEXT DEFAULT ''",
    "ALTER TABLE alignment_edges ADD COLUMN IF NOT EXISTS entity_type TEXT DEFAULT ''",
    "ALTER TABLE alignment_edges ADD COLUMN IF NOT EXISTS source_code_location TEXT DEFAULT ''",
    "ALTER TABLE alignment_edges ADD COLUMN IF NOT EXISTS target_code_location TEXT DEFAULT ''",
)


class GraphDBResource(ConfigurableResource):
    """Wraps both Neo4j and PostgreSQL for dual-write graph storage.

    Writes entities, assertions, and alignment edges to both:
    - PostgreSQL+pgvector (primary, for SQL queries and vector search)
    - Neo4j (traversal, for graph path queries)
    """

    # PostgreSQL
    pg_host: str = os.environ.get("KG_PG_HOST", "postgres-knowledge.catalyst-data.svc.cluster.local")
    pg_port: int = int(os.environ.get("KG_PG_PORT", "5432"))
    pg_database: str = os.environ.get("KG_PG_DATABASE", "knowledge_graph")
    pg_user: str = os.environ.get("KG_PG_USER", "kg")
    pg_password: str = os.environ.get("KG_PG_PASSWORD", "kg-homelab")

    # Neo4j
    neo4j_uri: str = os.environ.get("NEO4J_URI", "bolt://neo4j.catalyst-data.svc.cluster.local:7687")
    neo4j_user: str = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password: str = os.environ.get("NEO4J_PASSWORD", "neo4j-homelab")

    def _pg_conn(self):
        import psycopg

        return psycopg.connect(
            host=self.pg_host,
            port=self.pg_port,
            dbname=self.pg_database,
            user=self.pg_user,
            password=self.pg_password,
        )

    def _neo4j_driver(self):
        from neo4j import GraphDatabase

        return GraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_user, self.neo4j_password),
        )

    # -- PostgreSQL writes --

    @staticmethod
    def _ensure_canonical_entities_schema(cur) -> None:
        """Idempotently apply runtime migrations to ``canonical_entities``.

        Each statement in ``_CANONICAL_ENTITIES_MIGRATIONS`` uses
        ``ADD COLUMN IF NOT EXISTS`` so re-runs are cheap and safe. If a
        statement fails because the column already exists on an older
        Postgres that lacks ``IF NOT EXISTS`` support, we log and keep
        going — the upsert will surface any genuine schema mismatch on the
        subsequent INSERT.
        """
        for stmt in _CANONICAL_ENTITIES_MIGRATIONS:
            try:
                cur.execute(stmt)
            except Exception as exc:  # noqa: BLE001 — we intentionally keep going
                msg = str(exc).lower()
                if "already exists" in msg or "duplicate column" in msg:
                    logger.info(
                        "canonical_entities migration already applied: %s",
                        stmt.splitlines()[0],
                    )
                else:
                    raise

    def upsert_canonical_entities(self, entities: list[dict[str, Any]]) -> int:
        """Replace canonical entities in PostgreSQL.

        Uses DELETE + INSERT in one transaction instead of INSERT ON CONFLICT.
        Each canonical_entities run produces a complete new set of entities
        from re-clustering, so old canonical_ids that no longer exist in the
        new clustering must be removed — otherwise stale entities accumulate
        across runs (the canonical_id is a hash that changes when clusters
        change).
        """
        if not entities:
            return 0
        logger.info("Replacing %d canonical entities in PostgreSQL", len(entities))
        conn = self._pg_conn()
        try:
            with track_duration(
                GRAPH_DB_OPERATION_DURATION,
                {"backend": "postgresql", "operation": "upsert_entities"},
            ):
                with conn.cursor() as cur:
                    self._ensure_canonical_entities_schema(cur)
                    # Delete all existing entities — the new set is the
                    # complete truth. Cascade would handle FK refs, but
                    # we don't have cascading deletes set up, so this is
                    # safe as long as no other table references canonical_id
                    # with a RESTRICT constraint.
                    cur.execute("DELETE FROM canonical_entities")
                    deleted = cur.rowcount
                    if deleted:
                        logger.info("Deleted %d old canonical entities before re-insert", deleted)
                    for ent in entities:
                        cur.execute(
                            """
                        INSERT INTO canonical_entities (
                            canonical_id, canonical_name, entity_type, aliases,
                            external_ids, embedding, mention_count,
                            source_code_locations, source_candidate_ids,
                            first_seen, last_seen
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (canonical_id) DO UPDATE SET
                            canonical_name = EXCLUDED.canonical_name,
                            aliases = EXCLUDED.aliases,
                            external_ids = EXCLUDED.external_ids,
                            embedding = EXCLUDED.embedding,
                            mention_count = EXCLUDED.mention_count,
                            source_code_locations = EXCLUDED.source_code_locations,
                            source_candidate_ids = EXCLUDED.source_candidate_ids,
                            last_seen = EXCLUDED.last_seen
                        """,
                            (
                                ent["canonical_id"],
                                ent["canonical_name"],
                                ent["entity_type"],
                                ent.get("aliases", []),
                                json.dumps(ent.get("external_ids", {})),
                                ent.get("embedding"),
                                ent.get("mention_count", 0),
                                ent.get("source_code_locations") or [],
                                ent.get("source_candidate_ids") or [],
                                ent.get("first_seen"),
                                ent.get("last_seen"),
                            ),
                        )
                conn.commit()
                GRAPH_DB_OPERATIONS.labels(backend="postgresql", operation="upsert_entities").inc(len(entities))
                logger.info(
                    "PostgreSQL upsert_canonical_entities complete count=%d",
                    len(entities),
                )
                return len(entities)
        finally:
            conn.close()

    def upsert_alignment_edges(self, edges: list[dict[str, Any]]) -> int:
        """Replace alignment edges in PostgreSQL.

        Same rationale as upsert_canonical_entities: each run produces
        a complete new edge set from re-alignment, so stale edges must
        be deleted.
        """
        if not edges:
            return 0
        logger.info("Replacing %d alignment edges in PostgreSQL", len(edges))
        conn = self._pg_conn()
        try:
            with track_duration(
                GRAPH_DB_OPERATION_DURATION,
                {"backend": "postgresql", "operation": "upsert_edges"},
            ):
                with conn.cursor() as cur:
                    # Run schema migrations for new columns
                    for stmt in _ALIGNMENT_EDGES_MIGRATIONS:
                        cur.execute(stmt)
                    cur.execute("DELETE FROM alignment_edges")
                    deleted = cur.rowcount
                    if deleted:
                        logger.info("Deleted %d old alignment edges before re-insert", deleted)
                    for edge in edges:
                        cur.execute(
                            """
                        INSERT INTO alignment_edges (
                            edge_id, source_entity_id, target_entity_id,
                            alignment_type, score, evidence, method,
                            source_name, target_name, entity_type,
                            source_code_location, target_code_location
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (edge_id) DO UPDATE SET
                            score = EXCLUDED.score,
                            evidence = EXCLUDED.evidence,
                            source_name = EXCLUDED.source_name,
                            target_name = EXCLUDED.target_name,
                            entity_type = EXCLUDED.entity_type,
                            source_code_location = EXCLUDED.source_code_location,
                            target_code_location = EXCLUDED.target_code_location
                        """,
                            (
                                edge["edge_id"],
                                edge["source_entity_id"],
                                edge["target_entity_id"],
                                edge["alignment_type"],
                                edge["score"],
                                json.dumps(edge.get("evidence", [])),
                                edge.get("method", ""),
                                edge.get("source_name", ""),
                                edge.get("target_name", ""),
                                edge.get("entity_type", ""),
                                edge.get("source_code_location", ""),
                                edge.get("target_code_location", ""),
                            ),
                        )
                conn.commit()
                GRAPH_DB_OPERATIONS.labels(backend="postgresql", operation="upsert_edges").inc(len(edges))
                logger.info("PostgreSQL upsert_alignment_edges complete count=%d", len(edges))
                return len(edges)
        finally:
            conn.close()

    def upsert_assertions(self, assertions: list[dict[str, Any]]) -> int:
        """Replace assertions in PostgreSQL.

        Same rationale as upsert_canonical_entities: each run produces
        a complete set, stale assertion_ids must not accumulate.
        """
        if not assertions:
            return 0
        logger.info("Replacing %d assertions in PostgreSQL", len(assertions))
        conn = self._pg_conn()
        try:
            with track_duration(
                GRAPH_DB_OPERATION_DURATION,
                {"backend": "postgresql", "operation": "upsert_assertions"},
            ):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM assertions")
                    deleted = cur.rowcount
                    if deleted:
                        logger.info("Deleted %d old assertions before re-insert", deleted)
                    for a in assertions:
                        cur.execute(
                            """
                        INSERT INTO assertions (
                            assertion_id, subject_canonical_id, predicate,
                            predicate_canonical, object_canonical_id,
                            qualifiers, confidence, negated, hedged,
                            source_document_id, chunk_id, code_location
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (assertion_id) DO UPDATE SET
                            confidence = EXCLUDED.confidence,
                            qualifiers = EXCLUDED.qualifiers
                        """,
                            (
                                a["assertion_id"],
                                a.get("subject_canonical_id"),
                                a["predicate"],
                                a.get("predicate_canonical", ""),
                                a.get("object_canonical_id"),
                                json.dumps(a.get("qualifiers", {})),
                                a.get("confidence", 1.0),
                                a.get("negated", False),
                                a.get("hedged", False),
                                a.get("source_document_id"),
                                a.get("chunk_id"),
                                a.get("code_location", ""),
                            ),
                        )
                conn.commit()
                GRAPH_DB_OPERATIONS.labels(backend="postgresql", operation="upsert_assertions").inc(len(assertions))
                logger.info("PostgreSQL upsert_assertions complete count=%d", len(assertions))
                return len(assertions)
        finally:
            conn.close()

    # -- Speaker profiles (pgvector) --

    @staticmethod
    def _ensure_speaker_profiles_schema(cur) -> None:
        """Idempotently create speaker_profiles + speaker_profile_members tables."""
        cur.execute("""
            CREATE TABLE IF NOT EXISTS speaker_profiles (
                profile_id       TEXT PRIMARY KEY,
                centroid         vector(192) NOT NULL,
                display_name     TEXT,
                member_count     INT NOT NULL DEFAULT 0,
                total_duration_s REAL NOT NULL DEFAULT 0,
                first_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS speaker_profile_members (
                profile_id    TEXT REFERENCES speaker_profiles(profile_id),
                document_id   TEXT NOT NULL,
                local_label   TEXT NOT NULL,
                segment_count INT NOT NULL DEFAULT 0,
                PRIMARY KEY (profile_id, document_id, local_label)
            )
        """)
        # Safe to re-run — IF NOT EXISTS
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_speaker_profiles_centroid
            ON speaker_profiles USING ivfflat (centroid vector_cosine_ops)
        """)

    def load_speaker_profiles(self) -> list[dict[str, Any]]:
        """Load all speaker profiles from PostgreSQL."""
        conn = self._pg_conn()
        try:
            with (
                track_duration(
                    GRAPH_DB_OPERATION_DURATION,
                    {"backend": "postgresql", "operation": "load_speaker_profiles"},
                ),
                conn.cursor() as cur,
            ):
                self._ensure_speaker_profiles_schema(cur)
                cur.execute("""
                        SELECT profile_id, centroid, display_name, member_count,
                               total_duration_s, first_seen, last_seen
                        FROM speaker_profiles
                    """)
                rows = cur.fetchall()
                profiles = []
                for row in rows:
                    profiles.append(
                        {
                            "profile_id": row[0],
                            "centroid": list(row[1]) if row[1] else [],
                            "display_name": row[2],
                            "member_count": row[3],
                            "total_duration_s": row[4],
                            "first_seen": row[5].isoformat() if row[5] else "",
                            "last_seen": row[6].isoformat() if row[6] else "",
                        }
                    )
                GRAPH_DB_OPERATIONS.labels(backend="postgresql", operation="load_speaker_profiles").inc(len(profiles))
                logger.info("Loaded %d speaker profiles from PostgreSQL", len(profiles))
                return profiles
        finally:
            conn.close()

    def upsert_speaker_profiles(self, profiles: list[dict[str, Any]]) -> int:
        """Upsert speaker profiles + members into PostgreSQL."""
        if not profiles:
            return 0
        logger.info("Upserting %d speaker profiles to PostgreSQL", len(profiles))
        conn = self._pg_conn()
        try:
            with track_duration(
                GRAPH_DB_OPERATION_DURATION,
                {"backend": "postgresql", "operation": "upsert_speaker_profiles"},
            ):
                with conn.cursor() as cur:
                    self._ensure_speaker_profiles_schema(cur)
                    for prof in profiles:
                        cur.execute(
                            """
                            INSERT INTO speaker_profiles (
                                profile_id, centroid, display_name, member_count,
                                total_duration_s, first_seen, last_seen
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (profile_id) DO UPDATE SET
                                centroid = EXCLUDED.centroid,
                                display_name = EXCLUDED.display_name,
                                member_count = EXCLUDED.member_count,
                                total_duration_s = EXCLUDED.total_duration_s,
                                last_seen = EXCLUDED.last_seen
                            """,
                            (
                                prof["profile_id"],
                                prof.get("centroid"),
                                prof.get("display_name"),
                                prof.get("member_count", 0),
                                prof.get("total_duration_s", 0.0),
                                prof.get("first_seen"),
                                prof.get("last_seen"),
                            ),
                        )
                        # Upsert members
                        for member in prof.get("members", []):
                            cur.execute(
                                """
                                INSERT INTO speaker_profile_members (
                                    profile_id, document_id, local_label, segment_count
                                ) VALUES (%s, %s, %s, %s)
                                ON CONFLICT (profile_id, document_id, local_label) DO UPDATE SET
                                    segment_count = EXCLUDED.segment_count
                                """,
                                (
                                    prof["profile_id"],
                                    member["document_id"],
                                    member["local_label"],
                                    member.get("segment_count", 0),
                                ),
                            )
                conn.commit()
                GRAPH_DB_OPERATIONS.labels(backend="postgresql", operation="upsert_speaker_profiles").inc(len(profiles))
                logger.info("PostgreSQL upsert_speaker_profiles complete count=%d", len(profiles))
                return len(profiles)
        finally:
            conn.close()

    # -- Neo4j writes --

    def sync_entities_to_neo4j(self, entities: list[dict[str, Any]]) -> int:
        """Sync canonical entities to Neo4j as nodes."""
        if not entities:
            return 0
        logger.info("Syncing %d entities to Neo4j", len(entities))
        driver = self._neo4j_driver()
        try:
            with track_duration(
                GRAPH_DB_OPERATION_DURATION,
                {"backend": "neo4j", "operation": "sync_entities"},
            ):
                with driver.session() as session:
                    for ent in entities:
                        session.run(
                            """
                        MERGE (e:Entity {canonical_id: $canonical_id})
                        SET e.name = $name,
                            e.entity_type = $entity_type,
                            e.aliases = $aliases,
                            e.mention_count = $mention_count
                        """,
                            canonical_id=ent["canonical_id"],
                            name=ent["canonical_name"],
                            entity_type=ent["entity_type"],
                            aliases=ent.get("aliases", []),
                            mention_count=ent.get("mention_count", 0),
                        )
                GRAPH_DB_OPERATIONS.labels(backend="neo4j", operation="sync_entities").inc(len(entities))
                logger.info("Neo4j sync_entities complete count=%d", len(entities))
                return len(entities)
        finally:
            driver.close()

    def sync_alignment_edges_to_neo4j(self, edges: list[dict[str, Any]]) -> int:
        """Sync alignment edges to Neo4j as relationships."""
        if not edges:
            return 0
        logger.info("Syncing %d alignment edges to Neo4j", len(edges))
        driver = self._neo4j_driver()
        try:
            with track_duration(
                GRAPH_DB_OPERATION_DURATION,
                {"backend": "neo4j", "operation": "sync_edges"},
            ):
                with driver.session() as session:
                    for edge in edges:
                        rel_type = edge["alignment_type"].upper().replace(" ", "_")
                        session.run(
                            f"""
                        MATCH (a:Entity {{canonical_id: $source_id}})
                        MATCH (b:Entity {{canonical_id: $target_id}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r.score = $score, r.method = $method
                        """,
                            source_id=edge["source_entity_id"],
                            target_id=edge["target_entity_id"],
                            score=edge["score"],
                            method=edge.get("method", ""),
                        )
                GRAPH_DB_OPERATIONS.labels(backend="neo4j", operation="sync_edges").inc(len(edges))
                logger.info("Neo4j sync_alignment_edges complete count=%d", len(edges))
                return len(edges)
        finally:
            driver.close()

    def sync_assertions_to_neo4j(self, assertions: list[dict[str, Any]]) -> int:
        """Sync assertions to Neo4j as edges between entity nodes."""
        if not assertions:
            return 0
        logger.info("Syncing %d assertions to Neo4j", len(assertions))
        driver = self._neo4j_driver()
        count = 0
        try:
            with track_duration(
                GRAPH_DB_OPERATION_DURATION,
                {"backend": "neo4j", "operation": "sync_assertions"},
            ):
                with driver.session() as session:
                    for a in assertions:
                        subj_id = a.get("subject_canonical_id")
                        obj_id = a.get("object_canonical_id")
                        if not subj_id or not obj_id:
                            continue
                        session.run(
                            """
                        MATCH (s:Entity {canonical_id: $subj_id})
                        MATCH (o:Entity {canonical_id: $obj_id})
                        MERGE (s)-[r:ASSERTS {assertion_id: $assertion_id}]->(o)
                        SET r.predicate = $predicate,
                            r.confidence = $confidence,
                            r.negated = $negated,
                            r.hedged = $hedged
                        """,
                            subj_id=subj_id,
                            obj_id=obj_id,
                            assertion_id=a["assertion_id"],
                            predicate=a.get("predicate_canonical", a["predicate"]),
                            confidence=a.get("confidence", 1.0),
                            negated=a.get("negated", False),
                            hedged=a.get("hedged", False),
                        )
                        count += 1
                GRAPH_DB_OPERATIONS.labels(backend="neo4j", operation="sync_assertions").inc(count)
                logger.info("Neo4j sync_assertions complete count=%d", count)
                return count
        finally:
            driver.close()
