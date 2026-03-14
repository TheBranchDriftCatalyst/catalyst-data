"""Graph database resources — Neo4j + PostgreSQL dual-write."""

import json
import logging
import os
from typing import Any

from dagster import ConfigurableResource

logger = logging.getLogger(__name__)


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

    def upsert_canonical_entities(self, entities: list[dict[str, Any]]) -> int:
        """Upsert canonical entities into PostgreSQL."""
        if not entities:
            return 0
        conn = self._pg_conn()
        try:
            with conn.cursor() as cur:
                for ent in entities:
                    cur.execute(
                        """
                        INSERT INTO canonical_entities (
                            canonical_id, canonical_name, entity_type, aliases,
                            external_ids, embedding, mention_count,
                            first_seen, last_seen
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (canonical_id) DO UPDATE SET
                            canonical_name = EXCLUDED.canonical_name,
                            aliases = EXCLUDED.aliases,
                            external_ids = EXCLUDED.external_ids,
                            embedding = EXCLUDED.embedding,
                            mention_count = EXCLUDED.mention_count,
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
                            ent.get("first_seen"),
                            ent.get("last_seen"),
                        ),
                    )
            conn.commit()
            return len(entities)
        finally:
            conn.close()

    def upsert_alignment_edges(self, edges: list[dict[str, Any]]) -> int:
        """Upsert alignment edges into PostgreSQL."""
        if not edges:
            return 0
        conn = self._pg_conn()
        try:
            with conn.cursor() as cur:
                for edge in edges:
                    cur.execute(
                        """
                        INSERT INTO alignment_edges (
                            edge_id, source_entity_id, target_entity_id,
                            alignment_type, score, evidence, method
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (edge_id) DO UPDATE SET
                            score = EXCLUDED.score,
                            evidence = EXCLUDED.evidence
                        """,
                        (
                            edge["edge_id"],
                            edge["source_entity_id"],
                            edge["target_entity_id"],
                            edge["alignment_type"],
                            edge["score"],
                            json.dumps(edge.get("evidence", [])),
                            edge.get("method", ""),
                        ),
                    )
            conn.commit()
            return len(edges)
        finally:
            conn.close()

    def upsert_assertions(self, assertions: list[dict[str, Any]]) -> int:
        """Upsert assertions into PostgreSQL."""
        if not assertions:
            return 0
        conn = self._pg_conn()
        try:
            with conn.cursor() as cur:
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
            return len(assertions)
        finally:
            conn.close()

    # -- Neo4j writes --

    def sync_entities_to_neo4j(self, entities: list[dict[str, Any]]) -> int:
        """Sync canonical entities to Neo4j as nodes."""
        if not entities:
            return 0
        driver = self._neo4j_driver()
        try:
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
            return len(entities)
        finally:
            driver.close()

    def sync_alignment_edges_to_neo4j(self, edges: list[dict[str, Any]]) -> int:
        """Sync alignment edges to Neo4j as relationships."""
        if not edges:
            return 0
        driver = self._neo4j_driver()
        try:
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
            return len(edges)
        finally:
            driver.close()

    def sync_assertions_to_neo4j(self, assertions: list[dict[str, Any]]) -> int:
        """Sync assertions to Neo4j as edges between entity nodes."""
        if not assertions:
            return 0
        driver = self._neo4j_driver()
        count = 0
        try:
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
            return count
        finally:
            driver.close()
