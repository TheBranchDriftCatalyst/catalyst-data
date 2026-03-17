from __future__ import annotations

import json
from pathlib import Path

import pytest

from catalyst_contracts.audit.repository import AuditRepository


@pytest.fixture
def audit_path(tmp_path) -> Path:
    return tmp_path / "test-audit.jsonl"


@pytest.fixture
def audit(audit_path) -> AuditRepository:
    return AuditRepository(path=audit_path)


class TestAuditRepository:
    def test_record_and_read(self, audit, audit_path):
        entry = audit.record(
            tool_name="validate_mentions",
            verdict="valid",
            payload={"mentions": []},
            error_count=0,
            accepted=True,
        )
        assert entry["tool_name"] == "validate_mentions"
        assert entry["verdict"] == "valid"
        assert entry["accepted"] is True
        assert "timestamp" in entry
        assert "payload_hash" in entry

        entries = audit.read_all()
        assert len(entries) == 1
        assert entries[0]["tool_name"] == "validate_mentions"

    def test_append_only(self, audit):
        audit.record("tool_a", "valid", {}, 0, True)
        audit.record("tool_b", "invalid", {}, 2, False)

        entries = audit.read_all()
        assert len(entries) == 2
        assert entries[0]["tool_name"] == "tool_a"
        assert entries[1]["tool_name"] == "tool_b"

    def test_read_empty(self, audit):
        entries = audit.read_all()
        assert entries == []

    def test_payload_hash_deterministic(self, audit):
        e1 = audit.record("t", "valid", {"a": 1, "b": 2}, 0, True)
        e2 = audit.record("t", "valid", {"b": 2, "a": 1}, 0, True)
        assert e1["payload_hash"] == e2["payload_hash"]

    def test_creates_parent_directories(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "audit.jsonl"
        repo = AuditRepository(path=deep_path)
        repo.record("test", "valid", {}, 0, True)
        assert deep_path.exists()
