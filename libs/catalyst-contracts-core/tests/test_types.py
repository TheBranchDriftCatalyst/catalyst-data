"""Tests for shared base types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from catalyst_contracts_core.enums import ExtractionMethod
from catalyst_contracts_core.types import Provenance


class TestProvenance:
    def test_valid_construction(self):
        p = Provenance(source_document_id="doc-1", chunk_id="chunk-1")
        assert p.source_document_id == "doc-1"
        assert p.chunk_id == "chunk-1"

    def test_confidence_boundary_zero(self):
        p = Provenance(source_document_id="d", chunk_id="c", confidence=0.0)
        assert p.confidence == 0.0

    def test_confidence_boundary_one(self):
        p = Provenance(source_document_id="d", chunk_id="c", confidence=1.0)
        assert p.confidence == 1.0

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            Provenance(source_document_id="d", chunk_id="c", confidence=-0.01)

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            Provenance(source_document_id="d", chunk_id="c", confidence=1.01)

    def test_timestamp_auto_generated(self):
        p = Provenance(source_document_id="d", chunk_id="c")
        assert p.timestamp  # non-empty
        assert "T" in p.timestamp  # ISO format

    def test_span_fields_default_none(self):
        p = Provenance(source_document_id="d", chunk_id="c")
        assert p.span_start is None
        assert p.span_end is None

    def test_span_fields_set(self):
        p = Provenance(source_document_id="d", chunk_id="c", span_start=10, span_end=20)
        assert p.span_start == 10
        assert p.span_end == 20

    def test_extraction_method_default(self):
        p = Provenance(source_document_id="d", chunk_id="c")
        assert p.extraction_method == ExtractionMethod.LLM

    def test_extraction_method_override(self):
        p = Provenance(
            source_document_id="d", chunk_id="c",
            extraction_method=ExtractionMethod.SPACY,
        )
        assert p.extraction_method == ExtractionMethod.SPACY

    def test_default_empty_strings(self):
        p = Provenance(source_document_id="d", chunk_id="c")
        assert p.extraction_model == ""
        assert p.code_location == ""
