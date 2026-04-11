"""Pytest config for knowledge-graph tests.

These tests should not require a live Postgres / Neo4j — they use
``unittest.mock`` to stand in for ``psycopg.connect`` so CI doesn't need
docker-compose services running.
"""

from __future__ import annotations

import os

# Make the suite insensitive to whatever the local shell has set — the
# GraphDBResource reads these at class-def time, but the tests never
# actually open a real connection because they monkey-patch ``_pg_conn``.
os.environ.setdefault("KG_PG_HOST", "test-host")
os.environ.setdefault("KG_PG_PORT", "5432")
os.environ.setdefault("KG_PG_DATABASE", "test_kg")
os.environ.setdefault("KG_PG_USER", "test")
os.environ.setdefault("KG_PG_PASSWORD", "test")
