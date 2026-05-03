"""Tests for per-LLM-call timeout enforcement (CD-azmn).

Covers two protection layers added to defend against wedged Ollama / dead-TCP
hangs after the 9.5hr bench-wedge incident:

1. ``asyncio.wait_for`` wrapper around ``spo_pipeline.ainvoke(...)`` in
   ``_process_doc`` and ``_process_doc_spo_only`` — bounds each
   evidence-window invocation in wall-clock.
2. ``httpx.Timeout(read=...)`` propagation into the underlying ChatOpenAI /
   OpenAIEmbeddings clients — ensures a stalled READ trips per the
   configured deadline instead of blocking forever.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import httpx
import pytest

from dagster_io.extraction import _Doc, _per_call_timeout_s, _process_doc


@dataclass
class _MockChunk:
    document_id: str
    chunk_id: str
    text: str
    index: int = 0
    total_chunks: int = 1
    metadata: dict = field(default_factory=dict)


def _make_ner_result(num_windows: int = 1) -> dict:
    return {
        "stages": {
            "ner": {
                "accepted": [{"text": "Alice", "span_start": 0, "span_end": 5}],
                "retry_count": 0,
            }
        },
        "evidence_windows": [
            {
                "window_id": f"win-{i:04d}",
                "text": f"Window {i} text",
                "mention_indices": [0],
            }
            for i in range(num_windows)
        ],
        "entity_clusters": [],
        "audit_events": [],
        "status": "completed",
    }


def test_spo_ainvoke_wrapped_in_wait_for_raises_timeout(monkeypatch):
    """A wedged spo_pipeline.ainvoke trips asyncio.TimeoutError per LLM_PER_CALL_TIMEOUT."""
    # Force a tiny per-call timeout so the test runs fast.
    monkeypatch.setenv("LLM_PER_CALL_TIMEOUT", "0.2")

    ner_pipeline = AsyncMock()
    ner_pipeline.ainvoke.return_value = _make_ner_result(num_windows=1)

    async def _wedged_ainvoke(_state):
        # Sleep far longer than the configured per-call timeout. The
        # asyncio.wait_for guard added in extraction.py must surface this
        # as TimeoutError rather than letting the call hang.
        await asyncio.sleep(60.0)
        return {"stages": {"spo": {"accepted": [], "retry_count": 0}}, "status": "completed", "audit_events": []}

    spo_pipeline = AsyncMock()
    spo_pipeline.ainvoke.side_effect = _wedged_ainvoke

    doc = _Doc(
        doc_id="doc-test",
        full_text="Alice knows Bob.",
        chunks=[_MockChunk("doc-test", "chunk-0", "Alice knows Bob.")],
        chunk_metadata={},
    )

    async def _run():
        return await _process_doc(
            ner_pipeline=ner_pipeline,
            spo_pipeline=spo_pipeline,
            doc=doc,
            bench_model="test-model",
            max_retries=0,
        )

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        asyncio.run(_run())


def test_per_call_timeout_default_and_env_override(monkeypatch):
    """_per_call_timeout_s reads from env, falls back to 600s, ignores garbage."""
    monkeypatch.delenv("LLM_PER_CALL_TIMEOUT", raising=False)
    assert _per_call_timeout_s() == 600.0

    monkeypatch.setenv("LLM_PER_CALL_TIMEOUT", "42.5")
    assert _per_call_timeout_s() == 42.5

    monkeypatch.setenv("LLM_PER_CALL_TIMEOUT", "not-a-number")
    assert _per_call_timeout_s() == 600.0


def test_llmclient_passes_httpx_timeout_to_chatopenai(monkeypatch):
    """LLMClient must construct ChatOpenAI with an httpx.Timeout — not a scalar.

    A scalar timeout collapses connect/read/write into one budget; an
    httpx.Timeout with an explicit read= bound is what makes a wedged Ollama
    socket trip on the read deadline.
    """
    # Avoid a real langchain_openai network configuration probe.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from catalyst_langgraph.clients.llm import LLMClient

    client = LLMClient(model="gpt-4o-mini", timeout=42)
    underlying_timeout = client._chat_model.client._client.timeout
    assert isinstance(underlying_timeout, httpx.Timeout)
    assert underlying_timeout.read == 42.0
    # Connect / write / pool are bounded independently — small enough to
    # fail-fast on a dead TCP without hitting the read budget.
    assert underlying_timeout.connect == 10.0
    assert underlying_timeout.write == 10.0
