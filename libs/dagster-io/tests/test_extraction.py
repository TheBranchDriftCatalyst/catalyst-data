"""Tests for dagster_io.extraction — validated extraction via LangGraph.

Tests the extract_validated() helper and its components without requiring
actual LLM calls or MCP servers. Uses mocks for the LangGraph graph.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from dagster_io.extraction import extract_validated
from dagster_io.models import Assertion, Mention, MentionType


class FakeChunk:
    """Minimal TextChunk-like object for testing."""

    def __init__(self, text: str, document_id: str = "doc-1", chunk_id: str = "chunk-0"):
        self.text = text
        self.document_id = document_id
        self.chunk_id = chunk_id


def test_extract_validated_empty_chunks():
    """Empty input returns empty output."""
    mentions, assertions = extract_validated([], "test")
    assert mentions == []
    assert assertions == []


def test_extract_validated_returns_domain_models():
    """Results are Mention and Assertion domain model instances."""
    mock_result = {
        "accepted_mentions": [
            {
                "text": "Biden",
                "mention_type": "PERSON",
                "span_start": 0,
                "span_end": 5,
                "document_id": "doc-1",
                "chunk_id": "c0",
            },
        ],
        "accepted_propositions": [
            {"subject": "Biden", "predicate": "visited", "object": "Israel", "confidence": 0.9},
        ],
        "status": "completed",
        "mention_retry_count": 0,
        "proposition_retry_count": 0,
    }

    with patch("dagster_io.extraction._build_graph") as mock_build:
        mock_graph = MagicMock()

        async def fake_ainvoke(state):
            return mock_result

        mock_graph.ainvoke = fake_ainvoke
        mock_build.return_value = mock_graph

        chunks = [FakeChunk("Biden visited Israel")]
        mentions, assertions = extract_validated(chunks, "test")

    assert len(mentions) == 1
    assert isinstance(mentions[0], Mention)
    assert mentions[0].text == "Biden"
    assert mentions[0].mention_type == MentionType.PERSON

    assert len(assertions) == 1
    assert isinstance(assertions[0], Assertion)
    assert assertions[0].subject_text == "Biden"
    assert assertions[0].predicate == "visited"


def test_extract_validated_handles_unknown_entity_type():
    """Unknown mention types fall back to OTHER."""
    mock_result = {
        "accepted_mentions": [
            {"text": "test", "mention_type": "BOGUS_TYPE", "document_id": "doc-1", "chunk_id": "c0"},
        ],
        "accepted_propositions": [],
        "status": "completed",
        "mention_retry_count": 0,
        "proposition_retry_count": 0,
    }

    with patch("dagster_io.extraction._build_graph") as mock_build:
        mock_graph = MagicMock()

        async def fake_ainvoke(state):
            return mock_result

        mock_graph.ainvoke = fake_ainvoke
        mock_build.return_value = mock_graph

        mentions, _ = extract_validated([FakeChunk("test")], "test")

    assert mentions[0].mention_type == MentionType.OTHER


def test_extract_validated_new_entity_types():
    """New entity types (STRATEGIC_ASSET, BOOK, etc.) are recognized."""
    mock_result = {
        "accepted_mentions": [
            {"text": "Strait of Hormuz", "mention_type": "STRATEGIC_ASSET", "document_id": "d", "chunk_id": "c"},
            {"text": "The Art of the Deal", "mention_type": "BOOK", "document_id": "d", "chunk_id": "c"},
            {"text": "Secretary of State", "mention_type": "ROLE", "document_id": "d", "chunk_id": "c"},
            {"text": "Treasury bonds", "mention_type": "FINANCIAL_INSTRUMENT", "document_id": "d", "chunk_id": "c"},
            {"text": "Mueller Report", "mention_type": "DOCUMENT", "document_id": "d", "chunk_id": "c"},
        ],
        "accepted_propositions": [],
        "status": "completed",
        "mention_retry_count": 0,
        "proposition_retry_count": 0,
    }

    with patch("dagster_io.extraction._build_graph") as mock_build:
        mock_graph = MagicMock()

        async def fake_ainvoke(state):
            return mock_result

        mock_graph.ainvoke = fake_ainvoke
        mock_build.return_value = mock_graph

        mentions, _ = extract_validated([FakeChunk("test")], "test")

    types = {m.mention_type for m in mentions}
    assert MentionType.STRATEGIC_ASSET in types
    assert MentionType.BOOK in types
    assert MentionType.ROLE in types
    assert MentionType.FINANCIAL_INSTRUMENT in types
    assert MentionType.DOCUMENT in types


def test_extract_validated_multiple_chunks():
    """Multiple chunks produce aggregated results."""
    call_count = {"n": 0}

    async def fake_ainvoke(state):
        call_count["n"] += 1
        return {
            "accepted_mentions": [
                {"text": f"entity_{call_count['n']}", "mention_type": "PERSON", "document_id": "d", "chunk_id": "c"},
            ],
            "accepted_propositions": [],
            "status": "completed",
            "mention_retry_count": 0,
            "proposition_retry_count": 0,
        }

    with patch("dagster_io.extraction._build_graph") as mock_build:
        mock_graph = MagicMock()
        mock_graph.ainvoke = fake_ainvoke
        mock_build.return_value = mock_graph

        chunks = [FakeChunk(f"text {i}") for i in range(5)]
        mentions, _ = extract_validated(chunks, "test", max_concurrency=2)

    assert len(mentions) == 5


def test_extract_validated_failed_chunk_raises():
    """Permanent extraction failure raises, not silently returns None."""

    async def failing_ainvoke(state):
        raise RuntimeError("LLM exploded")

    with patch("dagster_io.extraction._build_graph") as mock_build:
        mock_graph = MagicMock()
        mock_graph.ainvoke = failing_ainvoke
        mock_build.return_value = mock_graph

        import pytest

        with pytest.raises(RuntimeError, match="LLM exploded"):
            extract_validated([FakeChunk("test")], "test")
