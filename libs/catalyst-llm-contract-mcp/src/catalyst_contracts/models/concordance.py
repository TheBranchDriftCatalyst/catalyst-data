from __future__ import annotations

from pydantic import BaseModel, Field


class ConcordanceCandidateScore(BaseModel):
    entity_id: str
    exact: float = Field(ge=0.0, le=1.0)
    substring: float = Field(ge=0.0, le=1.0)
    jaccard: float = Field(ge=0.0, le=1.0)
    cosine: float = Field(ge=0.0, le=1.0)
    combined: float = Field(ge=0.0, le=1.0)


class ConcordanceCandidateSet(BaseModel):
    mention_id: str
    candidates: list[ConcordanceCandidateScore]
    ambiguity_flag: bool = False
