from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class IssueCode(str, Enum):
    SPAN_MISMATCH = "SPAN_MISMATCH"
    INVALID_TYPE = "INVALID_TYPE"
    CONFIDENCE_OUT_OF_RANGE = "CONFIDENCE_OUT_OF_RANGE"
    DUPLICATE_SPAN = "DUPLICATE_SPAN"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    INVALID_PREDICATE = "INVALID_PREDICATE"
    COORDINATE_OUT_OF_RANGE = "COORDINATE_OUT_OF_RANGE"
    PRECISION_EXCEEDED = "PRECISION_EXCEEDED"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    SCORE_OUT_OF_RANGE = "SCORE_OUT_OF_RANGE"
    UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
    INCONSISTENT_SCORES = "INCONSISTENT_SCORES"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"


class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class EvidenceSpan(BaseModel):
    """A span of source text that serves as evidence for an extraction."""

    source_document_id: str = Field(description="ID of the source document containing this evidence")
    chunk_id: str | None = Field(default=None, description="ID of the chunk within the source document")
    span_start: int = Field(ge=0, description="Character offset where the evidence span starts")
    span_end: int = Field(description="Character offset where the evidence span ends")
    text: str = Field(description="The exact text of the evidence span")
    content_hash: str | None = Field(default=None, description="Hash of the evidence text for deduplication")

    @model_validator(mode="after")
    def validate_span(self) -> EvidenceSpan:
        if self.span_end <= self.span_start:
            raise ValueError("span_end must be greater than span_start")
        if len(self.text) != self.span_end - self.span_start:
            raise ValueError(
                f"text length ({len(self.text)}) must equal span_end - span_start "
                f"({self.span_end - self.span_start})"
            )
        return self


class ExtractionIssue(BaseModel):
    """A validation issue found during extraction quality checks."""

    code: IssueCode = Field(description="Machine-readable issue code")
    severity: IssueSeverity = Field(description="Severity level: error, warning, or info")
    message: str = Field(description="Human-readable description of the issue")
    path: str | None = Field(default=None, description="JSON path to the problematic field (e.g., 'mentions[0].span_start')")
