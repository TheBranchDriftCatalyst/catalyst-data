"""Tests for dagster_io.extraction — validated extraction via LangGraph.

Phase 2 (CD-j6d3): updated to patch ``_build_pipelines`` (the new NER+SPO
pipeline factory) rather than the deprecated ``_build_graph``.

Tests the extract_validated() helper and its components without requiring
actual LLM calls or MCP servers. Uses mocks for the LangGraph pipelines.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
        index: int = 0,
    ):
        self.text = text
        self.document_id = document_id
        self.chunk_id = chunk_id
        self.metadata = metadata or {}
        self.index = index


# ── Helpers for building Phase-2-compatible mock pipeline results ─────────────


def _make_ner_result(
    accepted_mentions: list[dict],
    evidence_windows: list[dict] | None = None,
    status: str = "completed",
) -> dict:
    """Build a mock NER pipeline ainvoke result (Phase 2 format)."""
    windows = evidence_windows or [
        {
            "window_id": "win-0000",
            "text": "Mock evidence text",
            "mention_indices": list(range(len(accepted_mentions))),
            "cluster_id": "cluster-0000",
            "doc_char_start": 0,
            "doc_char_end": 100,
        }
    ]
    return {
        "stages": {
            "ner": {
                "accepted": accepted_mentions,
                "retry_count": 0,
                "status": status,
            }
        },
        "evidence_windows": windows,
        "entity_clusters": [],
        "audit_events": [],
        "status": status,
    }


def _make_spo_result(accepted_propositions: list[dict], status: str = "completed") -> dict:
    """Build a mock SPO pipeline ainvoke result (Phase 2 format)."""
    return {
        "stages": {
            "spo": {
                "accepted": accepted_propositions,
                "retry_count": 0,
                "status": status,
            }
        },
        "audit_events": [],
        "status": status,
    }


def _patch_pipelines(ner_result: dict, spo_result: dict) -> tuple:
    """Return context-manager patcher + mock objects for ``_build_pipelines``."""
    mock_ner = AsyncMock()
    mock_ner.ainvoke.return_value = ner_result

    mock_spo = AsyncMock()
    mock_spo.ainvoke.return_value = spo_result

    mock_client = MagicMock()
    mock_client.structured_method = "mock"

    return patch(
        "dagster_io.extraction._build_pipelines",
        return_value=(mock_ner, mock_spo, mock_client, None),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_extract_validated_empty_chunks():
    """Empty input returns empty output."""
    mentions, assertions = extract_validated([], "test")
    assert mentions == []
    assert assertions == []


def test_extract_validated_returns_domain_models():
    """Results are Mention and Assertion domain model instances."""
    ner = _make_ner_result(
        accepted_mentions=[
            {
                "text": "Biden",
                "mention_type": "PERSON",
                "span_start": 0,
                "span_end": 5,
                "document_id": "doc-1",
                "chunk_id": "c0",
            }
        ],
        evidence_windows=[
            {
                "window_id": "win-0000",
                "text": "Biden visited Israel",
                "mention_indices": [0],
                "cluster_id": "cluster-0",
                "doc_char_start": 0,
                "doc_char_end": 20,
            }
        ],
    )
    spo = _make_spo_result(
        accepted_propositions=[{"subject": "Biden", "predicate": "visited", "object": "Israel", "confidence": 0.9}]
    )

    with _patch_pipelines(ner, spo):
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
    ner = _make_ner_result(
        accepted_mentions=[{"text": "test", "mention_type": "BOGUS_TYPE", "document_id": "doc-1", "chunk_id": "c0"}]
    )
    spo = _make_spo_result([])

    with _patch_pipelines(ner, spo):
        mentions, _ = extract_validated([FakeChunk("test")], "test")

    assert mentions[0].mention_type == MentionType.OTHER


def test_extract_validated_new_entity_types():
    """New entity types (STRATEGIC_ASSET, BOOK, etc.) are recognized."""
    ner = _make_ner_result(
        accepted_mentions=[
            {"text": "Strait of Hormuz", "mention_type": "STRATEGIC_ASSET", "document_id": "d", "chunk_id": "c"},
            {"text": "The Art of the Deal", "mention_type": "BOOK", "document_id": "d", "chunk_id": "c"},
            {"text": "Secretary of State", "mention_type": "ROLE", "document_id": "d", "chunk_id": "c"},
            {"text": "Treasury bonds", "mention_type": "FINANCIAL_INSTRUMENT", "document_id": "d", "chunk_id": "c"},
            {"text": "Mueller Report", "mention_type": "DOCUMENT", "document_id": "d", "chunk_id": "c"},
        ]
    )
    spo = _make_spo_result([])

    with _patch_pipelines(ner, spo):
        mentions, _ = extract_validated([FakeChunk("test")], "test")

    types = {m.mention_type for m in mentions}
    assert MentionType.STRATEGIC_ASSET in types
    assert MentionType.BOOK in types
    assert MentionType.ROLE in types
    assert MentionType.FINANCIAL_INSTRUMENT in types
    assert MentionType.DOCUMENT in types


def test_extract_validated_multiple_chunks():
    """Multiple chunks from different docs produce aggregated results.

    In Phase 2, each doc runs NER once → cluster → pack → SPO fan-out.
    5 chunks with distinct document_ids → 5 separate NER calls.
    """
    call_count = {"n": 0}

    def _make_ner_for_call():
        call_count["n"] += 1
        n = call_count["n"]
        return _make_ner_result(
            accepted_mentions=[
                {"text": f"entity_{n}", "mention_type": "PERSON", "document_id": f"d{n}", "chunk_id": f"c{n}"}
            ]
        )

    mock_ner = AsyncMock()
    mock_ner.ainvoke.side_effect = lambda _: asyncio_gather_helper(_make_ner_for_call())
    mock_spo = AsyncMock()
    mock_spo.ainvoke.return_value = _make_spo_result([])
    mock_client = MagicMock()
    mock_client.structured_method = "mock"

    async def _ner_ainvoke(state):
        return _make_ner_for_call()

    mock_ner.ainvoke = _ner_ainvoke

    with patch(
        "dagster_io.extraction._build_pipelines",
        return_value=(mock_ner, mock_spo, mock_client, None),
    ):
        # Give each chunk a unique document_id so they become 5 separate docs
        chunks = [FakeChunk(f"text {i}", document_id=f"doc-{i}", chunk_id=f"chunk-{i}") for i in range(5)]
        mentions, _ = extract_validated(chunks, "test", max_concurrency=2)

    assert len(mentions) == 5


def asyncio_gather_helper(coro):
    """Helper to make side_effect work with async."""
    return coro


def test_extract_validated_provenance_from_chunk_metadata():
    """Chunk temporal metadata propagates to mention/assertion provenance."""
    ner = _make_ner_result(
        accepted_mentions=[
            {
                "text": "Piers Morgan",
                "mention_type": "PERSON",
                "span_start": 0,
                "span_end": 12,
                "document_id": "media-video-123",
                "chunk_id": "media-video-123:chunk-42",
            }
        ],
        evidence_windows=[
            {
                "window_id": "win-0000",
                "text": "Piers Morgan interviews Nick Fuentes",
                "mention_indices": [0],
                "cluster_id": "c0",
                "doc_char_start": 0,
                "doc_char_end": 35,
            }
        ],
    )
    spo = _make_spo_result(
        [{"subject": "Piers Morgan", "predicate": "interviews", "object": "Nick Fuentes", "confidence": 0.95}]
    )

    with _patch_pipelines(ner, spo):
        chunk = FakeChunk(
            "Piers Morgan interviews Nick Fuentes",
            document_id="media-video-001",
            chunk_id="media-video-001:chunk-42",
            metadata={
                "start_s": 3600.5,
                "end_s": 3660.0,
                "speaker": "SPEAKER_00",
                "strategy": "speaker_turn",
            },
        )
        mentions, assertions = extract_validated([chunk], "media_ingest")

    assert len(mentions) == 1
    m = mentions[0]
    assert m.provenance is not None
    assert m.provenance.temporal_start_ms == 3600500
    assert m.provenance.temporal_end_ms == 3660000
    assert m.provenance.speaker_label == "SPEAKER_00"
    assert m.provenance.extraction_method == "llm"
    assert m.provenance.code_location == "media_ingest"
    assert m.provenance.span_start == 0
    assert m.provenance.span_end == 12

    assert len(assertions) == 1
    a = assertions[0]
    assert a.provenance is not None
    assert a.provenance.temporal_start_ms == 3600500
    assert a.provenance.speaker_label == "SPEAKER_00"
    assert a.provenance.code_location == "media_ingest"


def test_text_documents_always_get_provenance():
    """Even text documents without temporal/speaker data should get provenance."""
    ner = _make_ner_result(
        accepted_mentions=[
            {
                "text": "Apple Inc",
                "mention_type": "ORG",
                "span_start": 0,
                "span_end": 9,
                "document_id": "leak-456",
                "chunk_id": "leak-456:chunk-2",
            }
        ]
    )
    spo = _make_spo_result([])

    with _patch_pipelines(ner, spo):
        chunk = FakeChunk(
            "Apple Inc leaked docs",
            document_id="leak-456",
            chunk_id="leak-456:chunk-2",
            metadata={},
        )
        mentions, _ = extract_validated([chunk], "open_leaks")

    assert len(mentions) == 1
    m = mentions[0]
    assert m.provenance is not None
    assert m.provenance.source_document_id == "leak-456"
    assert m.provenance.span_start == 0
    assert m.provenance.span_end == 9
    assert m.provenance.code_location == "open_leaks"
    assert m.provenance.temporal_start_ms is None
    assert m.provenance.speaker_label is None


def test_assertions_link_to_mention_ids():
    """Assertions with matching subject/object text link to mention IDs."""
    ner = _make_ner_result(
        accepted_mentions=[
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
        ]
    )
    spo = _make_spo_result([{"subject": "Apple", "predicate": "acquired", "object": "Beats", "confidence": 1.0}])

    with _patch_pipelines(ner, spo):
        mentions, assertions = extract_validated([FakeChunk("Apple acquired Beats", chunk_id="d1:c0")], "test")

    assert len(assertions) == 1
    a = assertions[0]
    assert a.subject_mention_id != ""
    assert a.object_mention_id != ""
    mention_ids = {m.mention_id for m in mentions}
    assert a.subject_mention_id in mention_ids
    assert a.object_mention_id in mention_ids


def test_assertion_linkage_handles_missing_mentions():
    """Assertion referencing an entity not in mentions → empty mention_id."""
    ner = _make_ner_result(
        accepted_mentions=[{"text": "Apple", "mention_type": "ORG", "document_id": "d1", "chunk_id": "d1:c0"}]
    )
    spo = _make_spo_result([{"subject": "Apple", "predicate": "acquired", "object": "Unknown Corp", "confidence": 0.5}])

    with _patch_pipelines(ner, spo):
        _, assertions = extract_validated([FakeChunk("Apple acquired Unknown Corp", chunk_id="d1:c0")], "test")

    assert len(assertions) == 1
    a = assertions[0]
    assert a.subject_mention_id != "", "subject 'Apple' was extracted as a mention"
    assert a.object_mention_id == "", "object 'Unknown Corp' was NOT extracted as a mention"


def test_extract_validated_failed_chunk_raises():
    """Permanent extraction failure raises, not silently returns None."""
    mock_ner = AsyncMock()
    mock_ner.ainvoke.side_effect = RuntimeError("LLM exploded")
    mock_spo = AsyncMock()
    mock_client = MagicMock()
    mock_client.structured_method = "mock"

    with patch(
        "dagster_io.extraction._build_pipelines",
        return_value=(mock_ner, mock_spo, mock_client, None),
    ):
        import pytest

        with pytest.raises(RuntimeError, match="LLM exploded"):
            extract_validated([FakeChunk("test")], "test")
