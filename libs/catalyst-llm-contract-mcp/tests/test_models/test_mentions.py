from __future__ import annotations

import pytest
from pydantic import ValidationError

from catalyst_contracts.models.mentions import MentionExtraction


class TestMentionExtraction:
    def test_valid_mention(self):
        m = MentionExtraction(
            text="United Nations",
            mention_type="ORG",
            span_start=4,
            span_end=18,
            confidence=0.95,
        )
        assert m.text == "United Nations"
        assert m.mention_type == "ORG"
        assert m.evidence == []
        assert m.issues == []

    def test_confidence_lower_bound(self):
        with pytest.raises(ValidationError):
            MentionExtraction(
                text="x",
                mention_type="ORG",
                span_start=0,
                span_end=1,
                confidence=-0.1,
            )

    def test_confidence_upper_bound(self):
        with pytest.raises(ValidationError):
            MentionExtraction(
                text="x",
                mention_type="ORG",
                span_start=0,
                span_end=1,
                confidence=1.1,
            )

    def test_confidence_boundaries(self):
        m0 = MentionExtraction(
            text="x", mention_type="ORG", span_start=0, span_end=1, confidence=0.0
        )
        assert m0.confidence == 0.0

        m1 = MentionExtraction(
            text="x", mention_type="ORG", span_start=0, span_end=1, confidence=1.0
        )
        assert m1.confidence == 1.0

    def test_optional_context(self):
        m = MentionExtraction(
            text="x",
            mention_type="ORG",
            span_start=0,
            span_end=1,
            confidence=0.5,
            context="some context",
        )
        assert m.context == "some context"
