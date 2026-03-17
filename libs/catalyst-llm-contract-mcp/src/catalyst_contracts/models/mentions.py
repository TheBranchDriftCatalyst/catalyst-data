from __future__ import annotations

from pydantic import BaseModel, Field

from catalyst_contracts.models.evidence import EvidenceSpan, ExtractionIssue


class MentionExtraction(BaseModel):
    text: str
    mention_type: str
    span_start: int
    span_end: int
    context: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceSpan] = []
    issues: list[ExtractionIssue] = []
