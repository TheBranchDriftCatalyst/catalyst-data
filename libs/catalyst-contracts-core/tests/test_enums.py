"""Tests for shared enum types."""

from __future__ import annotations

import pytest

from catalyst_contracts_core.enums import AlignmentType, ExtractionMethod, MentionType


class TestMentionType:
    def test_all_values_exist(self):
        expected = {
            "PERSON", "ORG", "GPE", "LOC", "DATE",
            "LAW", "EVENT", "MONEY", "NORP", "FACILITY", "OTHER",
        }
        assert {t.value for t in MentionType} == expected

    def test_is_str_subclass(self):
        assert isinstance(MentionType.PERSON, str)
        assert MentionType.PERSON == "PERSON"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            MentionType("INVALID_TYPE")


class TestAlignmentType:
    def test_all_values_exist(self):
        expected = {"sameAs", "possibleSameAs", "relatedTo", "partOf"}
        assert {t.value for t in AlignmentType} == expected

    def test_is_str_subclass(self):
        assert isinstance(AlignmentType.SAME_AS, str)
        assert AlignmentType.SAME_AS == "sameAs"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            AlignmentType("invalid")


class TestExtractionMethod:
    def test_all_values_exist(self):
        expected = {"llm", "spacy", "regex", "manual", "structured"}
        assert {t.value for t in ExtractionMethod} == expected

    def test_is_str_subclass(self):
        assert isinstance(ExtractionMethod.LLM, str)
        assert ExtractionMethod.LLM == "llm"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ExtractionMethod("nonexistent")
