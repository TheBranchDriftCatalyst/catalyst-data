from __future__ import annotations

import pytest
from pydantic import ValidationError

from catalyst_contracts.models.evidence import (
    EvidenceSpan,
    ExtractionIssue,
    IssueCode,
    IssueSeverity,
)


class TestEvidenceSpan:
    def test_valid_span(self):
        span = EvidenceSpan(
            source_document_id="doc1",
            chunk_id="c1",
            span_start=0,
            span_end=5,
            text="hello",
        )
        assert span.text == "hello"
        assert span.span_end - span.span_start == len(span.text)

    def test_span_end_must_be_greater(self):
        with pytest.raises(ValidationError, match="span_end must be greater"):
            EvidenceSpan(
                source_document_id="doc1",
                span_start=5,
                span_end=3,
                text="ab",
            )

    def test_text_length_must_match_span(self):
        with pytest.raises(ValidationError, match="text length"):
            EvidenceSpan(
                source_document_id="doc1",
                span_start=0,
                span_end=5,
                text="hi",
            )

    def test_optional_fields(self):
        span = EvidenceSpan(
            source_document_id="doc1",
            span_start=0,
            span_end=3,
            text="abc",
        )
        assert span.chunk_id is None
        assert span.content_hash is None


class TestExtractionIssue:
    def test_create_issue(self):
        issue = ExtractionIssue(
            code=IssueCode.SPAN_MISMATCH,
            severity=IssueSeverity.ERROR,
            message="Text mismatch",
        )
        assert issue.code == IssueCode.SPAN_MISMATCH
        assert issue.severity == IssueSeverity.ERROR
        assert issue.path is None

    def test_issue_with_path(self):
        issue = ExtractionIssue(
            code=IssueCode.INVALID_TYPE,
            severity=IssueSeverity.WARNING,
            message="Bad type",
            path="mentions[0].type",
        )
        assert issue.path == "mentions[0].type"
