"""Tests for JsonlRepository."""

import json
from pathlib import Path

import pytest

from catalyst_langgraph.repository.jsonl import JsonlRepository


@pytest.mark.asyncio
async def test_save_mentions(tmp_path: Path):
    repo = JsonlRepository(tmp_path)
    mentions = [
        {"surface_form": "Acme Corp", "entity_type": "ORG"},
        {"surface_form": "John Smith", "entity_type": "PERSON"},
    ]
    await repo.save_mentions("doc-001", mentions)

    output_file = tmp_path / "doc-001" / "mentions.jsonl"
    assert output_file.exists()

    lines = output_file.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["surface_form"] == "Acme Corp"
    assert json.loads(lines[1])["entity_type"] == "PERSON"


@pytest.mark.asyncio
async def test_save_propositions(tmp_path: Path):
    repo = JsonlRepository(tmp_path)
    props = [{"subject": "John", "predicate": "works_for", "object": "Acme"}]
    await repo.save_propositions("doc-002", props)

    output_file = tmp_path / "doc-002" / "propositions.jsonl"
    assert output_file.exists()
    lines = output_file.read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["predicate"] == "works_for"


@pytest.mark.asyncio
async def test_save_audit_trail(tmp_path: Path):
    repo = JsonlRepository(tmp_path)
    events = [{"node_name": "extract_mentions", "status": "completed"}]
    await repo.save_audit_trail("doc-003", events)

    output_file = tmp_path / "doc-003" / "audit_trail.jsonl"
    assert output_file.exists()


@pytest.mark.asyncio
async def test_append_mode(tmp_path: Path):
    repo = JsonlRepository(tmp_path)
    await repo.save_mentions("doc-004", [{"a": 1}])
    await repo.save_mentions("doc-004", [{"b": 2}])

    output_file = tmp_path / "doc-004" / "mentions.jsonl"
    lines = output_file.read_text().strip().split("\n")
    assert len(lines) == 2
