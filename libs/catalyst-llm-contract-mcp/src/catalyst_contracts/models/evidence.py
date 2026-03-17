from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, model_validator


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


class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class EvidenceSpan(BaseModel):
    source_document_id: str
    chunk_id: str | None = None
    span_start: int
    span_end: int
    text: str
    content_hash: str | None = None

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
    code: IssueCode
    severity: IssueSeverity
    message: str
    path: str | None = None
