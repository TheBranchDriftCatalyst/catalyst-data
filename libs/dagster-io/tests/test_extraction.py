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

    def __init__(
        self,
        text: str,
        document_id: str = "doc-1",
        chunk_id: str = "chunk-0",
        metadata: dict | None = None,
    ):
        self.text = text
        self.document_id = document_id
        self.chunk_id = chunk_id
        self.metadata = metadata or {}


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
        mock_build.return_value = (mock_graph, MagicMock())

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
        mock_build.return_value = (mock_graph, MagicMock())

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
        mock_build.return_value = (mock_graph, MagicMock())

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
        mock_build.return_value = (mock_graph, MagicMock())

        chunks = [FakeChunk(f"text {i}") for i in range(5)]
        mentions, _ = extract_validated(chunks, "test", max_concurrency=2)

    assert len(mentions) == 5


def test_extract_validated_provenance_from_chunk_metadata():
    """Chunk temporal metadata propagates to mention/assertion provenance."""
    mock_result = {
        "accepted_mentions": [
            {
                "text": "Piers Morgan",
                "mention_type": "PERSON",
                "span_start": 0,
                "span_end": 12,
                "document_id": "doc-1",
                "chunk_id": "c0",
            },
        ],
        "accepted_propositions": [
            {"subject": "Piers Morgan", "predicate": "interviews", "object": "Nick Fuentes", "confidence": 0.95},
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
        mock_build.return_value = (mock_graph, MagicMock())

        chunk = FakeChunk(
            "Piers Morgan interviews Nick Fuentes",
            document_id="media-video-123",
            chunk_id="media-video-123:chunk-42",
            metadata={
                "start_s": 3600.5,
                "end_s": 3660.0,
                "speaker": "SPEAKER_00",
                "strategy": "speaker_turn",
            },
        )
        mentions, assertions = extract_validated([chunk], "media_ingest")

    # Mention should carry full provenance from chunk metadata
    assert len(mentions) == 1
    m = mentions[0]
    assert m.provenance is not None
    assert m.provenance.temporal_start_ms == 3600500  # 3600.5 * 1000
    assert m.provenance.temporal_end_ms == 3660000
    assert m.provenance.speaker_label == "SPEAKER_00"
    assert m.provenance.extraction_method == "llm"
    assert m.provenance.code_location == "media_ingest"
    assert m.provenance.extraction_model != ""  # should be populated from LLM_MODEL
    assert m.provenance.span_start == 0
    assert m.provenance.span_end == 12
    assert m.chunk_id == "media-video-123:chunk-42"

    # Assertion should also carry provenance
    assert len(assertions) == 1
    a = assertions[0]
    assert a.provenance is not None
    assert a.provenance.temporal_start_ms == 3600500
    assert a.provenance.speaker_label == "SPEAKER_00"
    assert a.provenance.code_location == "media_ingest"


def test_text_documents_always_get_provenance():
    """Even text documents without temporal/speaker data should get provenance.

    Scenario: A leaked document is extracted — no audio, no speaker.
    Then: The mention still has provenance with document_id, chunk_id,
    span positions, extraction model, and code location.
    """
    mock_result = {
        "accepted_mentions": [
            {
                "text": "Apple Inc",
                "mention_type": "ORG",
                "span_start": 0,
                "span_end": 9,
                "document_id": "leak-456",
                "chunk_id": "leak-456:chunk-2",
            },
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
        mock_build.return_value = (mock_graph, MagicMock())

        chunk = FakeChunk("Apple Inc leaked docs", document_id="leak-456", chunk_id="leak-456:chunk-2", metadata={})
        mentions, _ = extract_validated([chunk], "open_leaks")

    assert len(mentions) == 1
    m = mentions[0]
    assert m.provenance is not None, "Text documents must always have provenance"
    assert m.provenance.source_document_id == "leak-456"
    assert m.provenance.chunk_id == "leak-456:chunk-2"
    assert m.provenance.span_start == 0
    assert m.provenance.span_end == 9
    assert m.provenance.code_location == "open_leaks"
    assert m.provenance.temporal_start_ms is None  # no temporal data, and that's OK
    assert m.provenance.speaker_label is None


def test_assertions_link_to_mention_ids():
    """When an assertion's subject/object matches an extracted mention in the same
    chunk, the assertion's subject_mention_id and object_mention_id should be populated.

    Scenario: "Apple acquired Beats" is extracted as an assertion.
    Given: "Apple" and "Beats" were extracted as mentions from the same chunk.
    Then: The assertion links to those mention IDs.
    """
    mock_result = {
        "accepted_mentions": [
            {
                "text": "Apple",
                "mention_type": "ORG",
                "span_start": 0,
                "span_end": 5,
                "document_id": "d1",
                "chunk_id": "d1:c0",
            },
            {
                "text": "Beats",
                "mention_type": "ORG",
                "span_start": 15,
                "span_end": 20,
                "document_id": "d1",
                "chunk_id": "d1:c0",
            },
        ],
        "accepted_propositions": [
            {"subject": "Apple", "predicate": "acquired", "object": "Beats", "confidence": 1.0},
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
        mock_build.return_value = (mock_graph, MagicMock())

        mentions, assertions = extract_validated([FakeChunk("Apple acquired Beats", chunk_id="d1:c0")], "test")

    assert len(assertions) == 1
    a = assertions[0]
    assert a.subject_mention_id != "", "subject_mention_id should link to the Apple mention"
    assert a.object_mention_id != "", "object_mention_id should link to the Beats mention"

    # Verify the IDs match actual mention objects
    mention_ids = {m.mention_id for m in mentions}
    assert a.subject_mention_id in mention_ids
    assert a.object_mention_id in mention_ids


def test_assertion_linkage_handles_missing_mentions():
    """When an assertion references entities not found as mentions,
    mention IDs should be empty (not error).

    Scenario: SPO triple references an entity the NER stage missed.
    Then: The assertion is still created, but without mention linkage.
    """
    mock_result = {
        "accepted_mentions": [
            {"text": "Apple", "mention_type": "ORG", "document_id": "d1", "chunk_id": "d1:c0"},
        ],
        "accepted_propositions": [
            {"subject": "Apple", "predicate": "acquired", "object": "Unknown Corp", "confidence": 0.5},
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
        mock_build.return_value = (mock_graph, MagicMock())

        _, assertions = extract_validated([FakeChunk("Apple acquired Unknown Corp", chunk_id="d1:c0")], "test")

    assert len(assertions) == 1
    a = assertions[0]
    assert a.subject_mention_id != "", "subject 'Apple' was extracted as a mention"
    assert a.object_mention_id == "", "object 'Unknown Corp' was NOT extracted as a mention"


def test_extract_validated_failed_chunk_raises():
    """Permanent extraction failure raises, not silently returns None."""

    async def failing_ainvoke(state):
        raise RuntimeError("LLM exploded")

    with patch("dagster_io.extraction._build_graph") as mock_build:
        mock_graph = MagicMock()
        mock_graph.ainvoke = failing_ainvoke
        mock_build.return_value = (mock_graph, MagicMock())

        import pytest

        with pytest.raises(RuntimeError, match="LLM exploded"):
            extract_validated([FakeChunk("test")], "test")
