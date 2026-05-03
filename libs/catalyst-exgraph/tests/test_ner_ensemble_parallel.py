"""Test NerEnsembleNode parallel execution — mock 3 encoders, verify all 3 lists in output.

Phase A / CD-7h9m.  No GPU or Ollama required — _nodes entries are replaced
with lightweight callable mocks before each invocation.
"""

from __future__ import annotations

import asyncio

import pytest
from catalyst_exgraph.config import ner_stage_config
from catalyst_exgraph.nodes.ner_ensemble import NerEnsembleNode
from catalyst_exgraph.state import ExGraphState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mention(text: str, mention_type: str = "PERSON") -> dict:
    return {"text": text, "mention_type": mention_type, "span_start": 0, "span_end": len(text), "confidence": 0.9}


def _make_encoder_config(name: str):
    """StageConfig with model_override set to name so NerEnsembleNode keying works."""
    return ner_stage_config(model=name, max_retries=0)


def _make_mock_client(name: str):
    """Minimal ExtractionClient mock — not called when _nodes is replaced."""

    class _Client:
        model = name
        structured_method = "mock"

        async def structured_output(self, schema, messages):
            raise NotImplementedError("replaced by _MockNode")

    return _Client()


def _base_state(doc_id: str = "doc-test") -> ExGraphState:
    return {
        "raw_text": "Alice met Bob at Acme Corp.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


class _NodeStub:
    """Callable stub that acts like an ExtractNode returning a fixed mention list.

    NerEnsembleNode accesses ``node.config.stage_name`` so the stub carries a
    compatible ``.config`` attribute.
    """

    class config:
        stage_name = "ner"

    def __init__(self, mentions: list[dict]) -> None:
        self._mentions = list(mentions)

    async def __call__(self, sub_state: ExGraphState) -> dict:
        return {
            "stages": {
                "ner": {
                    "candidates": list(self._mentions),
                    "accepted": list(self._mentions),
                    "status": "completed",
                    "error": "",
                    "retry_count": 0,
                    "validation": {},
                }
            },
            "status": "completed",
            "audit_events": [],
        }


def _make_node_stub(mentions: list[dict]) -> _NodeStub:
    """Return a _NodeStub pre-loaded with the given mentions."""
    return _NodeStub(mentions)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_encoders_produce_separate_mention_lists():
    """NerEnsembleNode returns per_encoder_mentions with an entry for all 3 encoders."""
    enc_a_mentions = [_mention("Alice")]
    enc_b_mentions = [_mention("Bob"), _mention("Carol")]
    enc_c_mentions: list[dict] = []  # encoder C found nothing

    encoders = [
        _make_encoder_config("gliner-medium"),
        _make_encoder_config("gliner-large"),
        _make_encoder_config("gliner-small"),
    ]
    clients = {
        "gliner-medium": _make_mock_client("gliner-medium"),
        "gliner-large": _make_mock_client("gliner-large"),
        "gliner-small": _make_mock_client("gliner-small"),
    }

    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    # Replace each _nodes entry with a stub callable
    node._nodes["gliner-medium"] = _make_node_stub(enc_a_mentions)
    node._nodes["gliner-large"] = _make_node_stub(enc_b_mentions)
    node._nodes["gliner-small"] = _make_node_stub(enc_c_mentions)

    result = await node(_base_state())

    per_encoder = result["per_encoder_mentions"]
    assert set(per_encoder.keys()) == {"gliner-medium", "gliner-large", "gliner-small"}

    # Each encoder's own mentions are preserved separately
    assert len(per_encoder["gliner-medium"]) == 1
    assert per_encoder["gliner-medium"][0]["text"] == "Alice"

    assert len(per_encoder["gliner-large"]) == 2
    assert {m["text"] for m in per_encoder["gliner-large"]} == {"Bob", "Carol"}

    assert per_encoder["gliner-small"] == []


@pytest.mark.asyncio
async def test_encoder_mentions_tagged_with_source_encoder():
    """Every mention returned has _source_encoder set to its encoder name."""
    mentions = [_mention("Acme Corp", "ORG")]
    encoders = [_make_encoder_config("gliner-pii")]
    clients = {"gliner-pii": _make_mock_client("gliner-pii")}

    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes["gliner-pii"] = _make_node_stub(mentions)

    result = await node(_base_state())

    for m in result["per_encoder_mentions"]["gliner-pii"]:
        assert m["_source_encoder"] == "gliner-pii"


@pytest.mark.asyncio
async def test_no_encoders_returns_empty_dicts():
    """With zero encoders, both output dicts are empty."""
    node = NerEnsembleNode(encoders=[], clients={}, mcp_client=None)
    result = await node(_base_state())

    assert result["per_encoder_mentions"] == {}
    assert result["ensemble_errors"] == {}


@pytest.mark.asyncio
async def test_ensemble_errors_absent_when_all_succeed():
    """ensemble_errors is an empty dict when all encoders complete without error."""
    encoders = [_make_encoder_config("gliner-medium"), _make_encoder_config("gliner-large")]
    clients = {
        "gliner-medium": _make_mock_client("gliner-medium"),
        "gliner-large": _make_mock_client("gliner-large"),
    }
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes["gliner-medium"] = _make_node_stub([_mention("X")])
    node._nodes["gliner-large"] = _make_node_stub([_mention("Y")])

    result = await node(_base_state())

    assert result["ensemble_errors"] == {}


@pytest.mark.asyncio
async def test_parallel_execution_order_does_not_affect_result():
    """Results are keyed by name so execution order in gather doesn't matter."""

    class _SlowStub:
        class config:
            stage_name = "ner"

        async def __call__(self, sub_state):
            await asyncio.sleep(0.01)
            return {
                "stages": {
                    "ner": {
                        "candidates": [_mention("Slow")],
                        "accepted": [_mention("Slow")],
                        "status": "completed",
                        "error": "",
                        "retry_count": 0,
                        "validation": {},
                    }
                },
                "status": "completed",
                "audit_events": [],
            }

    class _FastStub:
        class config:
            stage_name = "ner"

        async def __call__(self, sub_state):
            return {
                "stages": {
                    "ner": {
                        "candidates": [_mention("Fast")],
                        "accepted": [_mention("Fast")],
                        "status": "completed",
                        "error": "",
                        "retry_count": 0,
                        "validation": {},
                    }
                },
                "status": "completed",
                "audit_events": [],
            }

    encoders = [_make_encoder_config("slow-enc"), _make_encoder_config("fast-enc")]
    clients = {
        "slow-enc": _make_mock_client("slow-enc"),
        "fast-enc": _make_mock_client("fast-enc"),
    }
    node = NerEnsembleNode(encoders=encoders, clients=clients, mcp_client=None)
    node._nodes["slow-enc"] = _SlowStub()
    node._nodes["fast-enc"] = _FastStub()

    result = await node(_base_state())

    assert result["per_encoder_mentions"]["slow-enc"][0]["text"] == "Slow"
    assert result["per_encoder_mentions"]["fast-enc"][0]["text"] == "Fast"
