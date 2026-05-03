"""Test NerEnsembleNode failure isolation — one encoder failure must not kill the run.

Phase A / CD-7h9m.  No GPU or Ollama required — _nodes entries are replaced
with lightweight callable stubs before each invocation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from catalyst_exgraph.config import ner_stage_config
from catalyst_exgraph.nodes.ner_ensemble import NerEnsembleNode
from catalyst_exgraph.state import ExGraphState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mention(text: str) -> dict:
    return {"text": text, "mention_type": "PERSON", "span_start": 0, "span_end": len(text), "confidence": 0.9}


def _make_encoder_config(name: str):
    return ner_stage_config(model=name, max_retries=0)


def _make_mock_client(name: str):
    class _Client:
        model = name
        structured_method = "mock"

        async def structured_output(self, schema, messages):
            raise NotImplementedError("replaced by stub node")

    return _Client()


def _base_state(doc_id: str = "doc-fail") -> ExGraphState:
    return {
        "raw_text": "Test sentence.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# Stub nodes — callable objects with .config.stage_name
# ---------------------------------------------------------------------------


class _GoodStub:
    class config:
        stage_name = "ner"

    async def __call__(self, sub_state):
        return {
            "stages": {
                "ner": {
                    "candidates": [
                        {"text": "Alice", "mention_type": "PERSON", "span_start": 0, "span_end": 5, "confidence": 0.9}
                    ],
                    "accepted": [
                        {"text": "Alice", "mention_type": "PERSON", "span_start": 0, "span_end": 5, "confidence": 0.9}
                    ],
                    "status": "completed",
                    "error": "",
                    "retry_count": 0,
                    "validation": {},
                }
            },
            "status": "completed",
            "audit_events": [],
        }


class _RaisingStub:
    class config:
        stage_name = "ner"

    async def __call__(self, sub_state):
        raise RuntimeError("model_crashed: CUDA OOM")


class _TimeoutStub:
    class config:
        stage_name = "ner"

    async def __call__(self, sub_state):
        await asyncio.sleep(999)  # simulates a wedged encoder
        return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_encoder_raises_others_complete():
    """When one encoder raises, others still produce their mentions."""
    encoders = [
        _make_encoder_config("good-enc"),
        _make_encoder_config("bad-enc"),
    ]
    clients = {
        "good-enc": _make_mock_client("good-enc"),
        "bad-enc": _make_mock_client("bad-enc"),
    }
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes["good-enc"] = _GoodStub()
    node._nodes["bad-enc"] = _RaisingStub()

    result = await node(_base_state())

    # Good encoder's mentions are intact
    assert len(result["per_encoder_mentions"]["good-enc"]) == 1
    assert result["per_encoder_mentions"]["good-enc"][0]["text"] == "Alice"

    # Bad encoder slot is empty (not absent)
    assert "bad-enc" in result["per_encoder_mentions"]
    assert result["per_encoder_mentions"]["bad-enc"] == []

    # Error recorded
    assert "bad-enc" in result["ensemble_errors"]
    err = result["ensemble_errors"]["bad-enc"]
    assert err["type"] == "RuntimeError"
    assert "CUDA OOM" in err["message"]


@pytest.mark.asyncio
async def test_timed_out_encoder_does_not_block_run():
    """A wedged encoder is cancelled at per_encoder_timeout_s; others complete."""
    encoders = [
        _make_encoder_config("good-enc"),
        _make_encoder_config("wedged-enc"),
    ]
    clients = {
        "good-enc": _make_mock_client("good-enc"),
        "wedged-enc": _make_mock_client("wedged-enc"),
    }
    node = NerEnsembleNode(
        encoders=encoders,
        clients=clients,
        mcp_client=None,
        per_encoder_timeout_s=0.05,  # 50 ms
    )
    node._nodes["good-enc"] = _GoodStub()
    node._nodes["wedged-enc"] = _TimeoutStub()

    result = await node(_base_state())

    assert len(result["per_encoder_mentions"]["good-enc"]) == 1
    assert result["per_encoder_mentions"]["wedged-enc"] == []

    err = result["ensemble_errors"]["wedged-enc"]
    assert err["type"] == "timeout"
    assert "duration_s" in err


@pytest.mark.asyncio
async def test_all_encoders_fail_produces_empty_lists():
    """All encoders failing leaves per_encoder_mentions with empty lists but no crash."""
    encoders = [_make_encoder_config("bad-a"), _make_encoder_config("bad-b")]
    clients = {
        "bad-a": _make_mock_client("bad-a"),
        "bad-b": _make_mock_client("bad-b"),
    }
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes["bad-a"] = _RaisingStub()
    node._nodes["bad-b"] = _RaisingStub()

    result = await node(_base_state())

    assert result["per_encoder_mentions"]["bad-a"] == []
    assert result["per_encoder_mentions"]["bad-b"] == []
    assert "bad-a" in result["ensemble_errors"]
    assert "bad-b" in result["ensemble_errors"]


@pytest.mark.asyncio
async def test_error_event_emitted_on_encoder_failure():
    """ner_encoder_completed with status='error' is written to the event tail."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)

    encoders = [_make_encoder_config("crash-enc")]
    clients = {"crash-enc": _make_mock_client("crash-enc")}
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes["crash-enc"] = _RaisingStub()

    await node(_base_state("doc-err"))

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    error_events = [e for e in events if e["node_name"] == "ner_encoder_completed" and e["status"] == "error"]
    assert len(error_events) == 1
    ev = error_events[0]
    assert ev["details"]["encoder"] == "crash-enc"
    assert ev["details"]["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_error_event_emitted_on_timeout():
    """ner_encoder_completed with status='error' and error='timeout' on timeout."""
    import dagster_io.bench.event_tail as et

    tail_path = Path(et._path)

    encoders = [_make_encoder_config("slow-enc")]
    clients = {"slow-enc": _make_mock_client("slow-enc")}
    node = NerEnsembleNode(
        encoders=encoders,
        clients=clients,
        mcp_client=None,
        per_encoder_timeout_s=0.05,
    )
    node._nodes["slow-enc"] = _TimeoutStub()

    await node(_base_state("doc-to"))

    events = [json.loads(line) for line in tail_path.read_text().splitlines() if line.strip()]
    error_events = [e for e in events if e["node_name"] == "ner_encoder_completed" and e["status"] == "error"]
    assert len(error_events) == 1
    ev = error_events[0]
    assert ev["details"]["error"] == "timeout"
    assert "timeout_s" in ev["details"]
