from __future__ import annotations

from pydantic import BaseModel, Field

from catalyst_contracts.models.evidence import EvidenceSpan, ExtractionIssue


class MentionExtraction(BaseModel):
    """A single entity mention extracted from source text with span offsets and metadata."""

    text: str = Field(description="The exact surface form as it appears in the source text")
    mention_type: str = Field(description="Entity type: PERSON, ORG, GPE, LOC, DATE, LAW, EVENT, MONEY, NORP, FACILITY, or OTHER")
    span_start: int = Field(ge=0, description="Character offset where the mention starts in the source text")
    span_end: int = Field(description="Character offset where the mention ends in the source text")
    context: str | None = Field(default=None, description="Surrounding text context for this mention")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score for this extraction, between 0 and 1")
    evidence: list[EvidenceSpan] = Field(default=[], description="Evidence spans supporting this mention")
    issues: list[ExtractionIssue] = Field(default=[], description="Validation issues found for this mention")
