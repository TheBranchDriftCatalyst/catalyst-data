"""Tests for mention_validator: ENTITY_TYPE_ALIASES and MISSING_REQUIRED_FIELD."""

from __future__ import annotations

import pytest

from catalyst_contracts.models.evidence import IssueCode
from catalyst_contracts.validators.mention_validator import (
    ENTITY_TYPE_ALIASES,
    validate_mentions,
)

SOURCE_TEXT = "The United Nations was founded in 1945 by 51 countries."


class TestMissingRequiredFieldEnum:
    def test_enum_member_exists(self):
        assert hasattr(IssueCode, "MISSING_REQUIRED_FIELD")
        assert IssueCode.MISSING_REQUIRED_FIELD.value == "MISSING_REQUIRED_FIELD"

    def test_missing_text_field(self):
        mentions = [
            {
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "invalid"
        assert any(
            e.code == IssueCode.MISSING_REQUIRED_FIELD.value for e in result.errors
        )

    def test_missing_mention_type_field(self):
        mentions = [
            {
                "text": "United Nations",
                "span_start": 4,
                "span_end": 18,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "invalid"
        assert any(
            e.code == IssueCode.MISSING_REQUIRED_FIELD.value for e in result.errors
        )


class TestEntityTypeAliases:
    """Verify each alias in ENTITY_TYPE_ALIASES resolves correctly."""

    @pytest.mark.parametrize(
        "alias, expected",
        [
            ("ORGANIZATION", "ORG"),
            ("LOCATION", "LOC"),
            ("GEOPOLITICAL_ENTITY", "GPE"),
            ("GEO_POLITICAL_ENTITY", "GPE"),
            ("GEOGRAPHIC", "LOC"),
            ("COMPANY", "ORG"),
            ("COUNTRY", "GPE"),
            ("CITY", "GPE"),
            ("STATE", "GPE"),
        ],
    )
    def test_alias_maps_to_expected(self, alias, expected):
        assert ENTITY_TYPE_ALIASES[alias] == expected

    def test_identity_types_not_in_aliases(self):
        """Types like PERSON, ORG, LOC are already canonical and should not appear as alias keys."""
        for canonical in ["PERSON", "ORG", "LOC", "GPE"]:
            assert canonical not in ENTITY_TYPE_ALIASES

    def test_mention_with_aliased_type_passes(self):
        """A mention using 'ORGANIZATION' instead of 'ORG' should pass validation."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORGANIZATION",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "valid"
        assert result.valid_count == 1

    def test_mention_with_company_alias(self):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "COMPANY",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "valid"

    def test_mention_with_location_alias(self):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "LOCATION",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "valid"

    def test_mention_with_country_alias(self):
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "COUNTRY",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "valid"

    def test_unknown_type_still_rejected(self):
        """A truly invalid type should still fail."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "FOOBAR",
                "span_start": 4,
                "span_end": 18,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "invalid"
        assert any(e.code == IssueCode.INVALID_TYPE.value for e in result.errors)

    def test_canonical_type_stays_canonical(self):
        """A canonical type like 'ORG' should pass without being aliased."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "ORG",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "valid"
        assert result.valid_count == 1

    def test_empty_string_mention_type(self):
        """Empty string mention_type should be treated as missing."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "",
                "span_start": 4,
                "span_end": 18,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "invalid"
        # Empty mention_type triggers MISSING_REQUIRED_FIELD (falsy check)
        assert any(
            e.code == IssueCode.MISSING_REQUIRED_FIELD.value for e in result.errors
        )

    def test_case_insensitive_alias_lookup(self):
        """Alias lookup should be case-insensitive (lowercase 'organization' works)."""
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "organization",
                "span_start": 4,
                "span_end": 18,
                "confidence": 0.95,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        # The code does .upper() before alias lookup, so lowercase should work
        assert result.verdict.value == "valid"

    def test_place_alias_not_defined(self):
        """'PLACE' is NOT in ENTITY_TYPE_ALIASES, should fail as invalid type."""
        assert "PLACE" not in ENTITY_TYPE_ALIASES
        mentions = [
            {
                "text": "United Nations",
                "mention_type": "PLACE",
                "span_start": 4,
                "span_end": 18,
            }
        ]
        result = validate_mentions(mentions, SOURCE_TEXT, "doc1")
        assert result.verdict.value == "invalid"
        assert any(e.code == IssueCode.INVALID_TYPE.value for e in result.errors)
