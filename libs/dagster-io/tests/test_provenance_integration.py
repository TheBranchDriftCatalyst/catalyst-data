"""Provenance integration tests — parametrized across all 3 domain types.

Verifies that the full provenance chain is populated correctly for
media (with temporal/speaker data), congress (text-only with sections),
and open-leaks (text-only leaked documents).

Uses mock extraction to avoid LLM calls, but exercises the REAL
provenance assembly code in extract_validated().

Run with: pytest libs/dagster-io/tests/test_provenance_integration.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dagster_io.extraction import extract_validated
from dagster_io.models import Assertion, Mention


class FakeChunk:
    """Minimal TextChunk-like object for testing."""

    def __init__(self, text, document_id="doc-1", chunk_id="doc-1:chunk-0", metadata=None):
        self.text = text
        self.document_id = document_id
        self.chunk_id = chunk_id
        self.metadata = metadata or {}


# ── Domain fixtures ─────────────────────────────────────────────────

MEDIA_CHUNK = FakeChunk(
    text="Piers Morgan sat down with Nick Fuentes in London on March 25, 2024.",
    document_id="media-video-001",
    chunk_id="media-video-001:chunk-3",
    metadata={
        "start_s": 120.5,
        "end_s": 180.0,
        "speaker": "SPEAKER_01",
        "strategy": "speaker_turn",
        "domain": "media",
        "chunk_char_offset": 4200,
    },
)

CONGRESS_CHUNK = FakeChunk(
    text="Section 101. The CHIPS and Science Act of 2022 authorizes $52 billion for domestic semiconductor manufacturing.",
    document_id="119-hr-4346",
    chunk_id="119-hr-4346:chunk-0",
    metadata={
        "strategy": "section_split",
        "domain": "congress",
        "chunk_char_offset": 0,
    },
)

LEAKS_CHUNK = FakeChunk(
    text="Maya Trading Company exported goods through the port of Djibouti on April 4, 2019.",
    document_id="leak-cable-9281",
    chunk_id="leak-cable-9281:chunk-1",
    metadata={
        "strategy": "recursive",
        "domain": "open_leaks",
        "chunk_char_offset": 1500,
    },
)


def _mock_extraction_result(chunk: FakeChunk) -> dict:
    """Build a realistic mock extraction result for a given chunk."""
    text = chunk.text

    # Pick entities from the text
    if "Piers Morgan" in text:
        mentions = [
            {"text": "Piers Morgan", "mention_type": "PERSON", "span_start": 0, "span_end": 12,
             "document_id": chunk.document_id, "chunk_id": chunk.chunk_id, "confidence": 0.95},
            {"text": "Nick Fuentes", "mention_type": "PERSON", "span_start": 28, "span_end": 40,
             "document_id": chunk.document_id, "chunk_id": chunk.chunk_id, "confidence": 0.92},
            {"text": "London", "mention_type": "GPE", "span_start": 44, "span_end": 50,
             "document_id": chunk.document_id, "chunk_id": chunk.chunk_id, "confidence": 0.99},
        ]
        propositions = [
            {"subject": "Piers Morgan", "predicate": "interviewed", "object": "Nick Fuentes", "confidence": 0.95},
        ]
    elif "CHIPS" in text:
        mentions = [
            {"text": "CHIPS and Science Act", "mention_type": "LAW", "span_start": 17, "span_end": 37,
             "document_id": chunk.document_id, "chunk_id": chunk.chunk_id, "confidence": 0.98},
            {"text": "$52 billion", "mention_type": "MONEY", "span_start": 65, "span_end": 76,
             "document_id": chunk.document_id, "chunk_id": chunk.chunk_id, "confidence": 0.97},
        ]
        propositions = [
            {"subject": "CHIPS and Science Act", "predicate": "authorizes", "object": "$52 billion", "confidence": 0.96},
        ]
    else:  # leaks
        mentions = [
            {"text": "Maya Trading Company", "mention_type": "ORG", "span_start": 0, "span_end": 20,
             "document_id": chunk.document_id, "chunk_id": chunk.chunk_id, "confidence": 0.90},
            {"text": "Djibouti", "mention_type": "GPE", "span_start": 58, "span_end": 66,
             "document_id": chunk.document_id, "chunk_id": chunk.chunk_id, "confidence": 0.93},
        ]
        propositions = [
            {"subject": "Maya Trading Company", "predicate": "exported_through", "object": "Djibouti", "confidence": 0.85},
        ]

    return {
        "accepted_mentions": mentions,
        "accepted_propositions": propositions,
        "status": "completed",
        "mention_retry_count": 0,
        "proposition_retry_count": 0,
    }


def _run_extraction(chunk: FakeChunk, code_location: str):
    """Run extract_validated with mocked graph, return (mentions, assertions)."""
    mock_result = _mock_extraction_result(chunk)

    with patch("dagster_io.extraction._build_graph") as mock_build:
        mock_graph = MagicMock()

        async def fake_ainvoke(state):
            return mock_result

        mock_graph.ainvoke = fake_ainvoke
        mock_build.return_value = (mock_graph, MagicMock())

        return extract_validated([chunk], code_location, max_concurrency=1)


# ── Parametrized Tests ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "chunk, code_location, domain",
    [
        (MEDIA_CHUNK, "media_ingest", "media"),
        (CONGRESS_CHUNK, "congress_data", "congress"),
        (LEAKS_CHUNK, "open_leaks", "open_leaks"),
    ],
    ids=["media", "congress", "open-leaks"],
)
class TestProvenanceByDomain:
    """Provenance must be complete for every domain — not just media."""

    def test_every_mention_has_provenance(self, chunk, code_location, domain):
        """Given extraction from any domain, every mention gets provenance."""
        mentions, _ = _run_extraction(chunk, code_location)
        assert len(mentions) > 0

        for m in mentions:
            assert m.provenance is not None, (
                f"[{domain}] Mention '{m.text}' has no provenance — "
                f"provenance must be created for ALL documents, not just media"
            )

    def test_provenance_has_source_document_and_chunk(self, chunk, code_location, domain):
        """Provenance traces back to the exact document and chunk."""
        mentions, _ = _run_extraction(chunk, code_location)

        for m in mentions:
            assert m.provenance.source_document_id == chunk.document_id, (
                f"[{domain}] Provenance.source_document_id should be '{chunk.document_id}'"
            )
            assert m.provenance.chunk_id == chunk.chunk_id, (
                f"[{domain}] Provenance.chunk_id should be '{chunk.chunk_id}'"
            )

    def test_provenance_has_span_positions(self, chunk, code_location, domain):
        """Provenance includes character span positions within the chunk."""
        mentions, _ = _run_extraction(chunk, code_location)

        for m in mentions:
            assert m.provenance.span_start is not None, (
                f"[{domain}] Provenance.span_start missing for '{m.text}'"
            )
            assert m.provenance.span_end is not None, (
                f"[{domain}] Provenance.span_end missing for '{m.text}'"
            )
            assert m.provenance.span_start < m.provenance.span_end

    def test_provenance_has_extraction_model(self, chunk, code_location, domain):
        """Provenance records which model produced the extraction."""
        mentions, _ = _run_extraction(chunk, code_location)

        for m in mentions:
            assert m.provenance.extraction_model != "", (
                f"[{domain}] Provenance.extraction_model is empty — "
                f"must record which LLM produced this extraction"
            )

    def test_provenance_has_code_location(self, chunk, code_location, domain):
        """Provenance records which Dagster code location ran the extraction."""
        mentions, _ = _run_extraction(chunk, code_location)

        for m in mentions:
            assert m.provenance.code_location == code_location, (
                f"[{domain}] Provenance.code_location should be '{code_location}', "
                f"got '{m.provenance.code_location}'"
            )

    def test_assertions_have_provenance(self, chunk, code_location, domain):
        """Assertions also get full provenance, not just mentions."""
        _, assertions = _run_extraction(chunk, code_location)
        assert len(assertions) > 0

        for a in assertions:
            assert a.provenance is not None, (
                f"[{domain}] Assertion '{a.subject_text} → {a.predicate} → {a.object_text}' "
                f"has no provenance"
            )
            assert a.provenance.code_location == code_location
            assert a.provenance.extraction_model != ""

    def test_assertions_link_to_mentions(self, chunk, code_location, domain):
        """Assertion subject/object mention IDs link to actual extracted mentions."""
        mentions, assertions = _run_extraction(chunk, code_location)
        mention_ids = {m.mention_id for m in mentions}

        for a in assertions:
            # Subject should link to a mention (both subject entities exist in our mock)
            assert a.subject_mention_id != "", (
                f"[{domain}] Assertion subject '{a.subject_text}' not linked to a mention"
            )
            assert a.subject_mention_id in mention_ids, (
                f"[{domain}] Assertion subject_mention_id '{a.subject_mention_id}' "
                f"not found in extracted mentions"
            )


class TestMediaSpecificProvenance:
    """Media documents carry temporal and speaker data that text documents don't."""

    def test_media_has_temporal_position(self):
        """Given a media chunk with timestamps, provenance includes temporal position."""
        mentions, _ = _run_extraction(MEDIA_CHUNK, "media_ingest")

        for m in mentions:
            assert m.provenance.temporal_start_ms == 120500, (
                "temporal_start_ms should be 120500 (120.5s * 1000)"
            )
            assert m.provenance.temporal_end_ms == 180000

    def test_media_has_speaker_label(self):
        """Given a media chunk with diarization, provenance includes speaker ID."""
        mentions, _ = _run_extraction(MEDIA_CHUNK, "media_ingest")

        for m in mentions:
            assert m.provenance.speaker_label == "SPEAKER_01"

    def test_text_documents_have_null_temporal(self):
        """Text documents (congress, leaks) have null temporal data — and that's correct."""
        for chunk, loc in [(CONGRESS_CHUNK, "congress_data"), (LEAKS_CHUNK, "open_leaks")]:
            mentions, _ = _run_extraction(chunk, loc)
            for m in mentions:
                assert m.provenance.temporal_start_ms is None
                assert m.provenance.speaker_label is None


class TestProvenanceSerializationRoundtrip:
    """Provenance must survive serialization to JSON and back."""

    def test_mention_provenance_roundtrips(self):
        """Serialize a mention to JSON and deserialize — provenance preserved."""
        mentions, _ = _run_extraction(MEDIA_CHUNK, "media_ingest")
        m = mentions[0]

        # Serialize
        json_str = m.model_dump_json()

        # Deserialize
        restored = Mention.model_validate_json(json_str)

        assert restored.provenance is not None
        assert restored.provenance.source_document_id == m.provenance.source_document_id
        assert restored.provenance.temporal_start_ms == m.provenance.temporal_start_ms
        assert restored.provenance.speaker_label == m.provenance.speaker_label
        assert restored.provenance.extraction_model == m.provenance.extraction_model
        assert restored.provenance.code_location == m.provenance.code_location

    def test_assertion_provenance_roundtrips(self):
        """Serialize an assertion to JSON and deserialize — provenance + mention links preserved."""
        mentions, assertions = _run_extraction(MEDIA_CHUNK, "media_ingest")
        a = assertions[0]

        json_str = a.model_dump_json()
        restored = Assertion.model_validate_json(json_str)

        assert restored.provenance is not None
        assert restored.subject_mention_id == a.subject_mention_id
        assert restored.provenance.code_location == "media_ingest"
