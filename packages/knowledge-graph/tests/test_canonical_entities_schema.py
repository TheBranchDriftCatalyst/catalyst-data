"""Smoke tests for CD-7np: source_code_locations column plumbing.

These tests mock out ``psycopg.connect`` and assert on the SQL emitted by
``GraphDBResource.upsert_canonical_entities``. They do NOT require a live
Postgres — the goal is to lock in the contract that:

1. A runtime ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS source_code_locations``
   migration is issued before any INSERT.
2. The INSERT column list includes ``source_code_locations``.
3. The ``ON CONFLICT ... DO UPDATE`` clause refreshes ``source_code_locations``
   on repeat inserts.
4. Entity dicts missing the field fall back to an empty list (legacy
   CanonicalEntity round-trip safety).
5. The authoritative ConfigMap schema in
   ``k8s/platform/postgres-knowledge.yaml`` declares the column too, so
   fresh PVCs materialize it on first boot.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# The test environment may not have ``psycopg`` installed. Install a
# lightweight stub BEFORE ``knowledge_graph.resources`` is imported so the
# lazy ``import psycopg`` inside ``_pg_conn`` succeeds. The real type of
# the object returned is irrelevant because we patch ``_pg_conn`` via a
# subclass below.
if "psycopg" not in sys.modules:
    _stub_psycopg = types.ModuleType("psycopg")
    _stub_psycopg.connect = MagicMock(name="psycopg.connect")  # type: ignore[attr-defined]
    sys.modules["psycopg"] = _stub_psycopg

from knowledge_graph.resources import (  # noqa: E402 — after stub install
    _CANONICAL_ENTITIES_MIGRATIONS,
    GraphDBResource,
)

# ---------------------------------------------------------------------------
# Schema / migration presence
# ---------------------------------------------------------------------------


def test_runtime_migration_declares_source_code_locations():
    """The module-level migration tuple carries the new column."""
    joined = "\n".join(_CANONICAL_ENTITIES_MIGRATIONS)
    assert "source_code_locations" in joined
    assert "ADD COLUMN IF NOT EXISTS" in joined
    assert "TEXT[]" in joined
    assert "NOT NULL" in joined


def test_init_sql_configmap_declares_source_code_locations():
    """The ConfigMap init.sql is the source of truth for fresh PVCs."""
    # tests/test_file.py -> tests -> knowledge-graph -> packages -> repo_root
    repo_root = Path(__file__).resolve().parents[3]
    init_sql_path = repo_root / "k8s" / "base" / "platform" / "postgres-knowledge.yaml"
    assert init_sql_path.exists(), f"expected {init_sql_path} to exist"
    init_sql = init_sql_path.read_text()
    assert "source_code_locations TEXT[] NOT NULL DEFAULT '{}'" in init_sql
    # The idempotent runtime migration should also be echoed in the
    # ConfigMap so the ConfigMap remains self-describing.
    assert "ADD COLUMN IF NOT EXISTS source_code_locations" in init_sql


# ---------------------------------------------------------------------------
# upsert_canonical_entities SQL assertions
# ---------------------------------------------------------------------------


# Module-level capture buffer — lives outside any pydantic class so
# ``ConfigurableResource`` doesn't try to coerce it into a field.
_captured_cursors: list[MagicMock] = []


class _FakeGraphDB(GraphDBResource):
    """Test double: ``_pg_conn`` returns a MagicMock conn + records its cursor."""

    def _pg_conn(self):  # type: ignore[override]
        fake_conn = MagicMock(name="psycopg_conn")
        fake_cursor = MagicMock(name="psycopg_cursor")
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
        fake_conn.cursor.return_value.__exit__.return_value = False
        _captured_cursors.append(fake_cursor)
        return fake_conn


def _make_resource() -> _FakeGraphDB:
    # Reset the per-test capture buffer.
    _captured_cursors.clear()
    return _FakeGraphDB(
        pg_host="test",
        pg_port=5432,
        pg_database="test",
        pg_user="test",
        pg_password="test",
    )


def _last_cursor() -> MagicMock:
    assert _captured_cursors, "no cursor captured — did upsert run?"
    return _captured_cursors[-1]


def test_upsert_issues_runtime_migration_before_insert():
    resource = _make_resource()

    resource.upsert_canonical_entities(
        [
            {
                "canonical_id": "abc-123",
                "canonical_name": "Acme",
                "entity_type": "ORG",
                "source_code_locations": ["media_ingest", "congress_data"],
            }
        ]
    )

    cursor = _last_cursor()
    executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("ADD COLUMN IF NOT EXISTS source_code_locations" in sql for sql in executed_sql), (
        f"runtime migration not issued. executed: {executed_sql}"
    )

    # Migration must precede the first INSERT.
    insert_idx = next(i for i, sql in enumerate(executed_sql) if "INSERT INTO canonical_entities" in sql)
    alter_idx = next(i for i, sql in enumerate(executed_sql) if "ADD COLUMN" in sql)
    assert alter_idx < insert_idx


def test_upsert_insert_sql_includes_source_code_locations():
    resource = _make_resource()

    resource.upsert_canonical_entities(
        [
            {
                "canonical_id": "abc-123",
                "canonical_name": "Acme",
                "entity_type": "ORG",
                "source_code_locations": ["congress_data", "media_ingest"],
            }
        ]
    )

    cursor = _last_cursor()
    insert_calls = [call for call in cursor.execute.call_args_list if "INSERT INTO canonical_entities" in call.args[0]]
    assert len(insert_calls) == 1
    sql, params = insert_calls[0].args
    assert "source_code_locations" in sql
    # ON CONFLICT clause must refresh the column too.
    assert "source_code_locations = EXCLUDED.source_code_locations" in sql
    # The param tuple should carry the list through verbatim.
    assert ["congress_data", "media_ingest"] in params


def test_upsert_defaults_missing_source_code_locations_to_empty_list():
    """Legacy CanonicalEntity dicts (pre-CD-7np) shouldn't crash the upsert."""
    resource = _make_resource()

    # No source_code_locations key at all.
    resource.upsert_canonical_entities(
        [
            {
                "canonical_id": "legacy-1",
                "canonical_name": "OldCorp",
                "entity_type": "ORG",
            }
        ]
    )

    cursor = _last_cursor()
    insert_calls = [call for call in cursor.execute.call_args_list if "INSERT INTO canonical_entities" in call.args[0]]
    assert len(insert_calls) == 1
    _, params = insert_calls[0].args
    assert [] in params, f"expected empty list default, got params={params}"


def test_ensure_schema_tolerates_already_exists():
    """If the ALTER raises 'already exists' we log and continue, not raise."""
    cursor = MagicMock()

    def _raise(stmt):
        raise RuntimeError('column "source_code_locations" of relation "canonical_entities" already exists')

    cursor.execute.side_effect = _raise

    # Should not raise.
    GraphDBResource._ensure_canonical_entities_schema(cursor)


def test_ensure_schema_propagates_unexpected_errors():
    """A non-benign error should bubble up so we notice real breakage."""
    import pytest

    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        GraphDBResource._ensure_canonical_entities_schema(cursor)


def test_legacy_none_source_code_locations_coerced():
    """``source_code_locations=None`` should still serialize as empty list."""
    resource = _make_resource()

    resource.upsert_canonical_entities(
        [
            {
                "canonical_id": "legacy-2",
                "canonical_name": "NullCorp",
                "entity_type": "ORG",
                "source_code_locations": None,  # legacy dict with explicit None
            }
        ]
    )

    cursor = _last_cursor()
    insert_calls = [call for call in cursor.execute.call_args_list if "INSERT INTO canonical_entities" in call.args[0]]
    _, params = insert_calls[0].args
    assert [] in params, f"expected empty list for None, got params={params}"
