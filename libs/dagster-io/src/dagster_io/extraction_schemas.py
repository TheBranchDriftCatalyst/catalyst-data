"""Shared Pydantic schemas for LLM extraction output.

These models define the structured output format that LLM chains return.
They are shared across all code locations (congress-data, open-leaks, etc.).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from catalyst_contracts_core.enums import MentionType

# ── Mention extraction ──────────────────────────────────────────────


class MentionExtraction(BaseModel):
    """A single mention extracted by the LLM."""

    text: str = Field(description="Entity mention as it appears in text")
    label: str = Field(description="Entity type: PERSON, ORG, GPE, LOC, DATE, LAW, EVENT, MONEY, NORP, FACILITY, OTHER")
    context: str = Field(description="Sentence fragment containing the entity")
    span_start: int = Field(description="Character offset start (0-based), or -1 if unknown")
    span_end: int = Field(description="Character offset end (exclusive), or -1 if unknown")


class MentionExtractionResult(BaseModel):
    """Structured output from mention extraction."""

    mentions: list[MentionExtraction] = Field(description="Extracted entity mentions")


def parse_mention_type(label: str) -> MentionType:
    """Parse LLM label string to MentionType enum, with fallback."""
    try:
        return MentionType(label.upper().strip())
    except ValueError:
        return MentionType.OTHER


# ── Assertion extraction ─────────────────────────────────────────────


class AssertionQualifiers(BaseModel):
    """Qualifier fields for an assertion."""

    time: str = Field(description="When this occurred, or empty string if unknown")
    location: str = Field(description="Where, or empty string if unknown")
    condition: str = Field(description="Under what condition, or empty string if none")
    manner: str = Field(description="How, or empty string if unknown")
    source_attribution: str = Field(description="Who says so, or empty string if not attributed")


class QualifiedAssertion(BaseModel):
    """A single qualified assertion extracted by the LLM."""

    subject: str = Field(description="Entity performing or being described")
    predicate: str = Field(description="Normalized relationship or action")
    object: str = Field(description="Target entity or value")
    confidence: float = Field(description="Score 0-1 indicating how clearly the text supports this")
    negated: bool = Field(description="True if this is a negative assertion")
    hedged: bool = Field(description="True if this is uncertain/hedged")
    qualifiers: AssertionQualifiers = Field(description="Qualifier fields for this assertion")


class AssertionExtractionResult(BaseModel):
    """Structured output from assertion extraction."""

    assertions: list[QualifiedAssertion] = Field(description="Extracted assertions")


def normalize_predicate(predicate: str, mappings: dict[str, str]) -> str:
    """Normalize a predicate string using a domain-specific mapping table."""
    return mappings.get(predicate.lower().strip(), predicate.lower().strip())
