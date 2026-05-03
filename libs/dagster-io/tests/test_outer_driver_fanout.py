"""Tests for the extract_validated outer driver — Phase 2 entity-anchored flow (CD-j6d3).

Tests the chunks→docs grouping and SPO fan-out logic using mock pipelines.
Does NOT run real LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from dagster_io.extraction import _Doc, _group_chunks_into_docs

# ── TextChunk mock ─────────────────────────────────────────────────────────


@dataclass
class _MockChunk:
    document_id: str
    chunk_id: str
    text: str
    index: int = 0
    total_chunks: int = 1
    metadata: dict = field(default_factory=dict)


# ── _group_chunks_into_docs tests ─────────────────────────────────────────


def test_group_single_doc():
    """Chunks from the same doc are grouped together."""
    chunks = [
        _MockChunk("doc-1", "chunk-0", "Hello", index=0),
        _MockChunk("doc-1", "chunk-1", "World", index=1),
    ]
    docs = _group_chunks_into_docs(chunks)
    assert len(docs) == 1
    assert docs[0].doc_id == "doc-1"
    assert "Hello" in docs[0].full_text
    assert "World" in docs[0].full_text


def test_group_multiple_docs():
    """Chunks from different docs produce separate _Doc objects."""
    chunks = [
        _MockChunk("doc-A", "chunk-0", "Alpha"),
        _MockChunk("doc-B", "chunk-0", "Beta"),
        _MockChunk("doc-A", "chunk-1", "Gamma", index=1),
    ]
    docs = _group_chunks_into_docs(chunks)
    assert len(docs) == 2
    doc_ids = {d.doc_id for d in docs}
    assert doc_ids == {"doc-A", "doc-B"}
    doc_a = next(d for d in docs if d.doc_id == "doc-A")
    assert "Alpha" in doc_a.full_text
    assert "Gamma" in doc_a.full_text


def test_group_preserves_chunk_order():
    """Chunks are concatenated in index order."""
    chunks = [
        _MockChunk("doc-1", "chunk-2", "Third", index=2),
        _MockChunk("doc-1", "chunk-0", "First", index=0),
        _MockChunk("doc-1", "chunk-1", "Second", index=1),
    ]
    docs = _group_chunks_into_docs(chunks)
    assert len(docs) == 1
    text = docs[0].full_text
    assert text.index("First") < text.index("Second") < text.index("Third")


def test_group_chunk_with_no_document_id():
    """Chunks without document_id are treated as their own doc."""
    chunks = [
        _MockChunk("", "chunk-0", "Orphan text"),
    ]
    # document_id is empty string → treated as "unknown"
    docs = _group_chunks_into_docs(chunks)
    assert len(docs) == 1
    assert docs[0].doc_id == "unknown"


# ── _process_doc SPO fan-out tests ────────────────────────────────────────


def _make_mock_ner_result(num_windows: int) -> dict:
    """Build a mock NER pipeline result with ``num_windows`` evidence windows."""
    windows = [
        {
            "window_id": f"win-{i:04d}",
            "text": f"Evidence text for window {i}",
            "mention_indices": [0],
            "cluster_id": f"cluster-{i:04d}",
            "doc_char_start": i * 100,
            "doc_char_end": i * 100 + 50,
        }
        for i in range(num_windows)
    ]
    return {
        "stages": {
            "ner": {
                "accepted": [{"text": "Alice", "span_start": 0, "span_end": 5}],
                "retry_count": 0,
            }
        },
        "evidence_windows": windows,
        "entity_clusters": [],
        "audit_events": [],
        "status": "completed",
    }


def _make_mock_spo_result() -> dict:
    """Build a mock SPO pipeline result with one accepted proposition."""
    return {
        "stages": {
            "spo": {
                "accepted": [{"subject": "Alice", "predicate": "knows", "object": "Bob", "confidence": 0.9}],
                "retry_count": 0,
            }
        },
        "audit_events": [],
        "status": "completed",
    }


@pytest.mark.asyncio
async def test_process_doc_spo_fanout_three_windows():
    """_process_doc with 3 evidence windows → exactly 3 SPO sub-graph invocations."""
    from dagster_io.extraction import _process_doc

    mock_ner_pipeline = AsyncMock()
    mock_ner_pipeline.ainvoke.return_value = _make_mock_ner_result(num_windows=3)

    mock_spo_pipeline = AsyncMock()
    mock_spo_pipeline.ainvoke.return_value = _make_mock_spo_result()

    doc = _Doc(
        doc_id="doc-test",
        full_text="Alice knows Bob. " * 50,
        chunks=[_MockChunk("doc-test", "chunk-0", "Alice knows Bob.")],
        chunk_metadata={},
    )

    result = await _process_doc(
        ner_pipeline=mock_ner_pipeline,
        spo_pipeline=mock_spo_pipeline,
        doc=doc,
        bench_model="test-model",
        max_retries=0,
    )

    # Exactly 3 SPO ainvoke calls (one per evidence window)
    assert mock_spo_pipeline.ainvoke.call_count == 3, (
        f"Expected 3 SPO invocations, got {mock_spo_pipeline.ainvoke.call_count}"
    )
    # NER called once
    assert mock_ner_pipeline.ainvoke.call_count == 1

    # SPO results accumulated
    assert len(result["propositions"]) == 3  # 1 per window
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_process_doc_spo_called_with_correct_mentions():
    """SPO invocation receives the correct evidence_window_id and upstream_context."""
    from dagster_io.extraction import _process_doc

    mock_ner_pipeline = AsyncMock()
    mock_ner_pipeline.ainvoke.return_value = _make_mock_ner_result(num_windows=1)

    mock_spo_pipeline = AsyncMock()
    mock_spo_pipeline.ainvoke.return_value = _make_mock_spo_result()

    doc = _Doc(
        doc_id="doc-xyz",
        full_text="Test text",
        chunks=[_MockChunk("doc-xyz", "chunk-0", "Test text")],
        chunk_metadata={},
    )

    await _process_doc(
        ner_pipeline=mock_ner_pipeline,
        spo_pipeline=mock_spo_pipeline,
        doc=doc,
        bench_model="test-model",
    )

    spo_call_kwargs = mock_spo_pipeline.ainvoke.call_args[0][0]
    assert spo_call_kwargs["evidence_window_id"] == "win-0000"
    assert spo_call_kwargs["doc_id"] == "doc-xyz"
    # accepted_mentions should be seeded from NER result
    assert spo_call_kwargs["upstream_context"]["accepted_mentions"][0]["text"] == "Alice"


@pytest.mark.asyncio
async def test_process_doc_no_windows_skips_spo():
    """When NER yields 0 evidence windows, SPO is never invoked."""
    from dagster_io.extraction import _process_doc

    mock_ner_pipeline = AsyncMock()
    mock_ner_pipeline.ainvoke.return_value = _make_mock_ner_result(num_windows=0)

    mock_spo_pipeline = AsyncMock()
    mock_spo_pipeline.ainvoke.return_value = _make_mock_spo_result()

    doc = _Doc(
        doc_id="doc-empty",
        full_text="No entities here",
        chunks=[_MockChunk("doc-empty", "chunk-0", "No entities here")],
        chunk_metadata={},
    )

    result = await _process_doc(
        ner_pipeline=mock_ner_pipeline,
        spo_pipeline=mock_spo_pipeline,
        doc=doc,
        bench_model="test-model",
    )

    assert mock_spo_pipeline.ainvoke.call_count == 0
    assert result["propositions"] == []
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_process_doc_failed_ner_propagates():
    """A failed NER result propagates as status='failed'."""
    from dagster_io.extraction import _process_doc

    mock_ner_pipeline = AsyncMock()
    failed_ner = _make_mock_ner_result(num_windows=0)
    failed_ner["status"] = "failed"
    mock_ner_pipeline.ainvoke.return_value = failed_ner

    mock_spo_pipeline = AsyncMock()

    doc = _Doc(
        doc_id="doc-fail",
        full_text="Failing text",
        chunks=[_MockChunk("doc-fail", "chunk-0", "Failing text")],
        chunk_metadata={},
    )

    result = await _process_doc(
        ner_pipeline=mock_ner_pipeline,
        spo_pipeline=mock_spo_pipeline,
        doc=doc,
        bench_model="test-model",
    )

    assert result["status"] == "failed"
    assert mock_spo_pipeline.ainvoke.call_count == 0
