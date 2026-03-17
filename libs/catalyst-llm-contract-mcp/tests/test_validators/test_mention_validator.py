from __future__ import annotations

from catalyst_contracts.models.validation import ValidationVerdict
from catalyst_contracts.validators.mention_validator import validate_mentions


class TestValidateMentions:
    def test_valid_mentions(self, valid_mentions_data, source_text):
        result = validate_mentions(valid_mentions_data, source_text, "doc1")
        assert result.verdict == ValidationVerdict.VALID
        assert result.valid_count == 3
        assert result.invalid_count == 0
        assert result.errors == []

    def test_invalid_mentions(self, invalid_mentions_data, source_text):
        result = validate_mentions(invalid_mentions_data, source_text, "doc1")
        assert result.verdict == ValidationVerdict.INVALID
        assert result.invalid_count == 2
        assert result.valid_count == 0
        assert len(result.errors) > 0

    def test_span_mismatch(self, source_text):
        mentions = [
            {
                "text": "WRONG",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.9,
            }
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "SPAN_MISMATCH" for e in result.errors)

    def test_invalid_mention_type(self, source_text):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "BOGUS",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.9,
            }
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "INVALID_TYPE" for e in result.errors)

    def test_confidence_out_of_range(self, source_text):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 1.5,
            }
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "CONFIDENCE_OUT_OF_RANGE" for e in result.errors)

    def test_duplicate_spans(self, source_text):
        mention = {
            "text": "United Nations",
            "mention_type": "ORG",
            "span_start": 4,
            "span_end": 18,
            "confidence": 0.9,
        }
        result = validate_mentions([mention, mention], source_text, "doc1")
        assert any(e.code == "DUPLICATE_SPAN" for e in result.errors)

    def test_mixed_valid_invalid(self, source_text):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.9,
            },
            {
                "text": "WRONG",
                "mention_type": "ORG",
                "span_start": 131,
                "span_end": 144,
                "confidence": 0.9,
            },
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert result.verdict == ValidationVerdict.AMBIGUOUS
        assert result.valid_count == 1
        assert result.invalid_count == 1

    def test_empty_mentions(self, source_text):
        result = validate_mentions([], source_text, "doc1")
        assert result.verdict == ValidationVerdict.INVALID
        assert result.valid_count == 0
        assert result.invalid_count == 0
        assert any(e.code == "EMPTY_EXTRACTION" for e in result.errors)

    def test_evidence_span_warning(self, source_text):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.9,
                "evidence": [
                    {
                        "source_document_id": "doc1",
                        "span_start": 0,
                        "span_end": 10,
                        "text": "short",
                    }
                ],
            }
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert len(result.warnings) > 0

    def test_confidence_boundary_zero_valid(self, source_text):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.0,
            }
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert result.verdict == ValidationVerdict.VALID

    def test_confidence_boundary_one_valid(self, source_text):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 1.0,
            }
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert result.verdict == ValidationVerdict.VALID

    def test_invalid_span_range(self, source_text):
        """Cover lines 52-53: span_start/span_end present but invalid range."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 18,
                "span_end": 4,  # end < start -> invalid range
                "confidence": 0.9,
            }
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert result.verdict == ValidationVerdict.INVALID
        assert any(
            e.code == "SPAN_MISMATCH" and "Invalid span range" in e.message
            for e in result.errors
        )

    def test_span_exceeds_source_length(self, source_text):
        """span_end beyond source text length hits the elif branch."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": len(source_text) + 100,  # beyond source length
                "confidence": 0.9,
            }
        ]
        result = validate_mentions(mentions, source_text, "doc1")
        assert result.verdict == ValidationVerdict.INVALID
        assert any(
            e.code == "SPAN_MISMATCH" and "Invalid span range" in e.message
            for e in result.errors
        )
