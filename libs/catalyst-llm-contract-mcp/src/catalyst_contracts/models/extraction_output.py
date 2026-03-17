"""Canonical LLM output models for structured extraction.

These models define the exact schema that LLMs must emit when using
``with_structured_output()``.  Field names match what the validators expect
(``text``, ``mention_type``, ``span_start``, ``span_end``), eliminating the
field-name mismatch that occurs with free-form prompting.

The Pydantic class name, docstring, and Field descriptions are all injected
into the tool/function schema the LLM sees, so they serve as both documentation
and prompt engineering.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MentionCandidate(BaseModel):
    """A single named entity mention extracted from source text."""

    text: str = Field(
        description="The exact surface form as it appears in the source text"
    )
    mention_type: str = Field(
        description=(
            "Entity type, one of: PERSON, ORG, GPE, LOC, DATE, LAW, "
            "EVENT, MONEY, NORP, FACILITY, OTHER"
        )
    )
    span_start: int = Field(
        ge=0,
        description="Character offset where the mention starts in the source text",
    )
    span_end: int = Field(
        description="Character offset where the mention ends in the source text"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for this extraction, between 0 and 1",
    )


class MentionExtractionResult(BaseModel):
    """Result of entity mention extraction from text.

    Return a JSON object with a ``mentions`` array containing all named entity
    mentions found in the source text.
    """

    mentions: list[MentionCandidate] = Field(
        description="All named entity mentions extracted from the source text"
    )


class PropositionCandidate(BaseModel):
    """A Subject-Predicate-Object triple extracted from text."""

    subject: str = Field(
        description="The subject entity text (should match an accepted mention text)"
    )
    predicate: str = Field(
        description="The relationship verb or phrase (prefer snake_case, e.g., 'works_for')"
    )
    object: str = Field(
        description="The object entity text (should match an accepted mention text)"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score for this proposition, between 0 and 1",
    )
    evidence: str = Field(
        default="",
        description="The source text span supporting this triple",
    )


class PropositionExtractionResult(BaseModel):
    """Result of proposition extraction from text.

    Return a JSON object with a ``propositions`` array containing all
    Subject-Predicate-Object triples found in the source text.
    """

    propositions: list[PropositionCandidate] = Field(
        description="All propositions (SPO triples) extracted from the source text"
    )
