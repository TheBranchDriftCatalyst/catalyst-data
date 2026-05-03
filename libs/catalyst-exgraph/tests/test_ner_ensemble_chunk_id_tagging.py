"""Test NerEnsembleNode chunk_id tagging — events must use '{doc_id}:_ner_{encoder_name}'.

Phase A / CD-7h9m.  Verifies observability contract: the State Inspector uses
chunk_id to identify one card per (doc, encoder).  No GPU or Ollama required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from catalyst_exgraph.config import ner_stage_config
from catalyst_exgraph.nodes.ner_ensemble import NerEnsembleNode
from catalyst_exgraph.state import ExGraphState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_encoder_config(name: str):
    return ner_stage_config(model=name, max_retries=0)


def _make_mock_client(name: str):
    class _Client:
        model = name
        structured_method = "mock"

        async def structured_output(self, schema, messages):
            raise NotImplementedError("replaced by stub node")

    return _Client()


def _base_state(doc_id: str) -> ExGraphState:
    return {
        "raw_text": "Test.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


class _OkStub:
    """Stub node that returns an empty completed NER stage."""

    class config:
        stage_name = "ner"

    async def __call__(self, sub_state: ExGraphState) -> dict:
        return {
            "stages": {
                "ner": {
                    "candidates": [],
                    "accepted": [],
                    "status": "completed",
                    "error": "",
                    "retry_count": 0,
                    "validation": {},
                }
            },
            "status": "completed",
            "audit_events": [],
        }


class _FailingStub:
    """Stub node that always raises ValueError."""

    class config:
        stage_name = "ner"

    async def __call__(self, sub_state: ExGraphState) -> dict:
        raise ValueError("boom")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_started_event_chunk_id_pattern():
    """ner_encoder_started event has chunk_id = '{doc_id}:_ner_{encoder_name}'."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)
    doc_id = "doc-abc"
    encoder_name = "gliner-medium"

    encoders = [_make_encoder_config(encoder_name)]
    clients = {encoder_name: _make_mock_client(encoder_name)}
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes[encoder_name] = _OkStub()

    await node(_base_state(doc_id))

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    started = [e for e in events if e["node_name"] == "ner_encoder_started"]
    assert len(started) == 1

    expected_chunk_id = f"{doc_id}:_ner_{encoder_name}"
    assert started[0]["chunk_id"] == expected_chunk_id


@pytest.mark.asyncio
async def test_completed_event_chunk_id_pattern():
    """ner_encoder_completed event has chunk_id = '{doc_id}:_ner_{encoder_name}'."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)
    doc_id = "doc-xyz"
    encoder_name = "gliner-large"

    encoders = [_make_encoder_config(encoder_name)]
    clients = {encoder_name: _make_mock_client(encoder_name)}
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes[encoder_name] = _OkStub()

    await node(_base_state(doc_id))

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    completed = [e for e in events if e["node_name"] == "ner_encoder_completed"]
    assert len(completed) == 1

    expected_chunk_id = f"{doc_id}:_ner_{encoder_name}"
    assert completed[0]["chunk_id"] == expected_chunk_id


@pytest.mark.asyncio
async def test_each_encoder_gets_distinct_chunk_id():
    """With 3 encoders, 3 distinct chunk_ids appear in the event stream."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)
    doc_id = "doc-multi"
    encoder_names = ["gliner-medium", "gliner-large", "gliner-pii"]

    encoders = [_make_encoder_config(n) for n in encoder_names]
    clients = {n: _make_mock_client(n) for n in encoder_names}
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    for n in encoder_names:
        node._nodes[n] = _OkStub()

    await node(_base_state(doc_id))

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    ner_events = [e for e in events if e["node_name"] in ("ner_encoder_started", "ner_encoder_completed")]

    observed_chunk_ids = {e["chunk_id"] for e in ner_events}
    expected_chunk_ids = {f"{doc_id}:_ner_{n}" for n in encoder_names}

    assert expected_chunk_ids.issubset(observed_chunk_ids), (
        f"Missing chunk_ids: {expected_chunk_ids - observed_chunk_ids}"
    )


@pytest.mark.asyncio
async def test_model_field_matches_encoder_name_on_events():
    """Every ner_encoder_* event has model == encoder_name."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)
    doc_id = "doc-model-check"
    encoder_name = "nuextract-2.0-8b"

    encoders = [_make_encoder_config(encoder_name)]
    clients = {encoder_name: _make_mock_client(encoder_name)}
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes[encoder_name] = _OkStub()

    await node(_base_state(doc_id))

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    ner_events = [e for e in events if "ner_encoder" in e.get("node_name", "")]

    assert len(ner_events) >= 1
    for ev in ner_events:
        assert ev["model"] == encoder_name, f"Expected model={encoder_name!r}, got {ev['model']!r} in {ev}"


@pytest.mark.asyncio
async def test_error_event_also_carries_correct_chunk_id():
    """Even on failure the ner_encoder_completed error event uses the right chunk_id."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)
    doc_id = "doc-err-chunk"
    encoder_name = "bad-encoder"

    encoders = [_make_encoder_config(encoder_name)]
    clients = {encoder_name: _make_mock_client(encoder_name)}
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes[encoder_name] = _FailingStub()

    await node(_base_state(doc_id))

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    error_ev = next(
        (e for e in events if e["node_name"] == "ner_encoder_completed" and e["status"] == "error"),
        None,
    )
    assert error_ev is not None
    assert error_ev["chunk_id"] == f"{doc_id}:_ner_{encoder_name}"


@pytest.mark.asyncio
async def test_chunk_id_uses_doc_id_from_state_not_source_metadata():
    """chunk_id is built from state['doc_id'] (top-level), not source_metadata."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)
    # Put a different doc_id in source_metadata to verify precedence
    encoder_name = "gliner-medium"
    state: ExGraphState = {
        "raw_text": "Hello world.",
        "doc_id": "top-level-doc-id",
        "source_metadata": {"document_id": "meta-doc-id"},
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }

    encoders = [_make_encoder_config(encoder_name)]
    clients = {encoder_name: _make_mock_client(encoder_name)}
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes[encoder_name] = _OkStub()

    await node(state)

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    started = [e for e in events if e["node_name"] == "ner_encoder_started"]
    assert started[0]["chunk_id"] == f"top-level-doc-id:_ner_{encoder_name}"


@pytest.mark.asyncio
async def test_chunk_extracted_emitted_per_encoder():
    """chunk_extracted fires for each encoder with the correct chunk_id and model."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)
    doc_id = "doc-extracted-check"
    encoder_names = ["gliner-medium", "gliner-large"]

    encoders = [_make_encoder_config(n) for n in encoder_names]
    clients = {n: _make_mock_client(n) for n in encoder_names}
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    for n in encoder_names:
        node._nodes[n] = _OkStub()

    await node(_base_state(doc_id))

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    extracted = [e for e in events if e["node_name"] == "chunk_extracted"]

    # One chunk_extracted per encoder
    assert len(extracted) == len(encoder_names), (
        f"Expected {len(encoder_names)} chunk_extracted events, got {len(extracted)}"
    )

    for enc_name in encoder_names:
        expected_chunk_id = f"{doc_id}:_ner_{enc_name}"
        match = next((e for e in extracted if e["chunk_id"] == expected_chunk_id), None)
        assert match is not None, f"No chunk_extracted for chunk_id={expected_chunk_id!r}"
        assert match["model"] == enc_name, f"Expected model={enc_name!r}, got {match['model']!r}"
        # details must carry mention/proposition counts
        d = match["details"]
        assert "mention_count" in d
        assert "proposition_count" in d
