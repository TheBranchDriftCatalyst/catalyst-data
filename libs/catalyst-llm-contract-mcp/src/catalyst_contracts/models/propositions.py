from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Discriminator, Field, Tag


class PropositionArgument(BaseModel):
    """An argument in an n-ary proposition, linking a role to a mention."""

    role: str = Field(description="The semantic role of this argument (e.g., agent, patient, instrument)")
    mention_id: str | None = Field(default=None, description="Composite ID of the referenced mention (e.g., 'ORG:0:9')")
    text: str | None = Field(default=None, description="Surface text of this argument if no mention ID is available")


class BinaryProposition(BaseModel):
    """A Subject-Predicate-Object triple extracted from text."""

    kind: Literal["binary"] = Field(default="binary", description="Discriminator: always 'binary' for SPO triples")
    subject_text: str | None = Field(default=None, description="The subject entity surface text")
    subject_id: str | None = Field(default=None, description="Composite ID of the subject mention (e.g., 'PERSON:20:30')")
    predicate: str = Field(description="The relationship verb or phrase in snake_case (e.g., 'works_for')")
    object_text: str | None = Field(default=None, description="The object entity surface text")
    object_id: str | None = Field(default=None, description="Composite ID of the object mention (e.g., 'ORG:0:9')")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score for this proposition, between 0 and 1")
    negated: bool = Field(default=False, description="Whether this proposition is negated")
    hedged: bool = Field(default=False, description="Whether this proposition is hedged or uncertain")
    qualifiers: dict[str, str] = Field(default={}, description="Additional qualifiers for this proposition (e.g., temporal, spatial)")


class NaryProposition(BaseModel):
    """An n-ary proposition with multiple typed arguments."""

    kind: Literal["nary"] = Field(default="nary", description="Discriminator: always 'nary' for multi-argument propositions")
    predicate: str = Field(description="The relationship verb or phrase in snake_case")
    arguments: list[PropositionArgument] = Field(description="Typed arguments for this proposition")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score for this proposition, between 0 and 1")


Proposition = Annotated[
    Union[
        Annotated[BinaryProposition, Tag("binary")],
        Annotated[NaryProposition, Tag("nary")],
    ],
    Discriminator("kind"),
]


class PropositionExtraction(BaseModel):
    """Wrapper for a single extracted proposition with discriminated union type."""

    proposition: Proposition = Field(description="The extracted proposition (binary or n-ary)")
