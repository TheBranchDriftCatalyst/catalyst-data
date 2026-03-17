from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Discriminator, Field, Tag


class PropositionArgument(BaseModel):
    role: str
    mention_id: str | None = None
    text: str | None = None


class BinaryProposition(BaseModel):
    kind: Literal["binary"] = "binary"
    subject_text: str | None = None
    subject_id: str | None = None
    predicate: str
    object_text: str | None = None
    object_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    negated: bool = False
    hedged: bool = False
    qualifiers: dict[str, str] = {}


class NaryProposition(BaseModel):
    kind: Literal["nary"] = "nary"
    predicate: str
    arguments: list[PropositionArgument]
    confidence: float = Field(ge=0.0, le=1.0)


Proposition = Annotated[
    Union[
        Annotated[BinaryProposition, Tag("binary")],
        Annotated[NaryProposition, Tag("nary")],
    ],
    Discriminator("kind"),
]


class PropositionExtraction(BaseModel):
    proposition: Proposition
