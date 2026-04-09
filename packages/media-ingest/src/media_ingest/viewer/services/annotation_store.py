"""PostgreSQL-backed annotation store for human feedback on pipeline outputs.

Stores annotations and speaker mappings in the existing postgres-knowledge
instance. Tables are created idempotently on first connection. All operations
are safe — errors are logged and return defaults so the viewer never crashes
if PostgreSQL is unavailable.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from dagster_io.logging import get_logger

logger = get_logger(__name__)

# ── Connection config (defaults match postgres-knowledge k8s service) ────────

_PG_CONFIG = {
    "host": os.getenv("KG_PG_HOST", "postgres-knowledge.catalyst-data.svc.cluster.local"),
    "port": int(os.getenv("KG_PG_PORT", "5432")),
    "dbname": os.getenv("KG_PG_DB", "knowledge_graph"),
    "user": os.getenv("KG_PG_USER", "kg"),
    "password": os.getenv("KG_PG_PASSWORD", "kg-homelab"),
}

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS viewer_annotations (
    annotation_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action TEXT NOT NULL,
    edits JSONB DEFAULT '{}',
    reviewer TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_va_doc ON viewer_annotations(document_id);
CREATE INDEX IF NOT EXISTS idx_va_target ON viewer_annotations(target_id);

CREATE TABLE IF NOT EXISTS viewer_speaker_mappings (
    document_id TEXT NOT NULL,
    speaker_label TEXT NOT NULL,
    display_name TEXT NOT NULL,
    color_index INTEGER,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (document_id, speaker_label)
);
CREATE INDEX IF NOT EXISTS idx_vsm_doc ON viewer_speaker_mappings(document_id);
"""


class AnnotationStore:
    """Lightweight PostgreSQL store for viewer annotations."""

    def __init__(self) -> None:
        self._conn = None
        self._initialized = False

    def _get_conn(self):
        if self._conn is not None and not self._conn.closed:
            return self._conn
        try:
            import psycopg
            self._conn = psycopg.connect(**_PG_CONFIG, autocommit=True)
            if not self._initialized:
                self._conn.execute(_INIT_SQL)
                self._initialized = True
                logger.info("Annotation store connected to %s:%s/%s", _PG_CONFIG["host"], _PG_CONFIG["port"], _PG_CONFIG["dbname"])
            return self._conn
        except Exception as e:
            logger.warning("Annotation store connection failed: %s", e)
            self._conn = None
            return None

    def _safe(self, fn, default=None):
        """Execute fn with connection, return default on error."""
        conn = self._get_conn()
        if conn is None:
            return default
        try:
            return fn(conn)
        except Exception as e:
            logger.warning("Annotation store error: %s", e)
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            return default

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

    # ── Annotations CRUD ─────────────────────────────────────────────────

    def list_annotations(self, document_id: str) -> list[dict]:
        def _query(conn):
            rows = conn.execute(
                "SELECT annotation_id, document_id, target_type, target_id, action, edits, reviewer, notes, created_at "
                "FROM viewer_annotations WHERE document_id = %s ORDER BY created_at DESC",
                (document_id,),
            ).fetchall()
            return [
                {
                    "annotation_id": r[0], "document_id": r[1], "target_type": r[2],
                    "target_id": r[3], "action": r[4], "edits": r[5] or {},
                    "reviewer": r[6], "notes": r[7],
                    "created_at": r[8].isoformat() if r[8] else None,
                }
                for r in rows
            ]
        return self._safe(_query, [])

    def create_annotation(self, data: dict) -> dict | None:
        def _insert(conn):
            aid = data.get("annotation_id", str(uuid.uuid4()))
            import json
            conn.execute(
                "INSERT INTO viewer_annotations (annotation_id, document_id, target_type, target_id, action, edits, reviewer, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (aid, data["document_id"], data["target_type"], data["target_id"],
                 data["action"], json.dumps(data.get("edits", {})),
                 data.get("reviewer", ""), data.get("notes", "")),
            )
            return {"annotation_id": aid, **data}
        return self._safe(_insert)

    def update_annotation(self, annotation_id: str, data: dict) -> dict | None:
        def _update(conn):
            import json
            sets = []
            vals = []
            for key in ("action", "reviewer", "notes"):
                if key in data:
                    sets.append(f"{key} = %s")
                    vals.append(data[key])
            if "edits" in data:
                sets.append("edits = %s")
                vals.append(json.dumps(data["edits"]))
            if not sets:
                return None
            vals.append(annotation_id)
            conn.execute(f"UPDATE viewer_annotations SET {', '.join(sets)} WHERE annotation_id = %s", vals)
            return {"annotation_id": annotation_id, **data}
        return self._safe(_update)

    def delete_annotation(self, annotation_id: str) -> bool:
        def _delete(conn):
            conn.execute("DELETE FROM viewer_annotations WHERE annotation_id = %s", (annotation_id,))
            return True
        return self._safe(_delete, False)

    def bulk_create_annotations(self, annotations: list[dict]) -> int:
        def _bulk(conn):
            import json
            count = 0
            for data in annotations:
                aid = data.get("annotation_id", str(uuid.uuid4()))
                conn.execute(
                    "INSERT INTO viewer_annotations (annotation_id, document_id, target_type, target_id, action, edits, reviewer, notes) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (annotation_id) DO NOTHING",
                    (aid, data["document_id"], data["target_type"], data["target_id"],
                     data["action"], json.dumps(data.get("edits", {})),
                     data.get("reviewer", ""), data.get("notes", "")),
                )
                count += 1
            return count
        return self._safe(_bulk, 0)

    # ── Speaker Mappings ─────────────────────────────────────────────────

    def get_speaker_mappings(self, document_id: str) -> dict[str, dict]:
        def _query(conn):
            rows = conn.execute(
                "SELECT speaker_label, display_name, color_index FROM viewer_speaker_mappings WHERE document_id = %s",
                (document_id,),
            ).fetchall()
            return {r[0]: {"display_name": r[1], "color_index": r[2]} for r in rows}
        return self._safe(_query, {})

    def save_speaker_mappings(self, document_id: str, mappings: dict[str, str]) -> bool:
        def _upsert(conn):
            for label, name in mappings.items():
                conn.execute(
                    "INSERT INTO viewer_speaker_mappings (document_id, speaker_label, display_name, updated_at) "
                    "VALUES (%s, %s, %s, NOW()) "
                    "ON CONFLICT (document_id, speaker_label) DO UPDATE SET display_name = EXCLUDED.display_name, updated_at = NOW()",
                    (document_id, label, name),
                )
            return True
        return self._safe(_upsert, False)
