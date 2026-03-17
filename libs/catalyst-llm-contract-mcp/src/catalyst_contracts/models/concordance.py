from __future__ import annotations

from pydantic import BaseModel, Field


class ConcordanceCandidateScore(BaseModel):
    """Similarity scores for a candidate entity match in concordance resolution."""

    entity_id: str = Field(description="ID of the candidate entity in the knowledge graph")
    exact: float = Field(ge=0.0, le=1.0, description="Exact string match score")
    substring: float = Field(ge=0.0, le=1.0, description="Substring match score")
    jaccard: float = Field(ge=0.0, le=1.0, description="Jaccard token similarity score")
    cosine: float = Field(ge=0.0, le=1.0, description="Cosine embedding similarity score")
    combined: float = Field(ge=0.0, le=1.0, description="Weighted combined similarity score")


class ConcordanceCandidateSet(BaseModel):
    """A set of candidate entity matches for a mention, used in concordance resolution."""

    mention_id: str = Field(description="Composite ID of the mention being resolved (e.g., 'ORG:0:9')")
    candidates: list[ConcordanceCandidateScore] = Field(description="Ranked list of candidate entity matches")
    ambiguity_flag: bool = Field(default=False, description="Whether multiple candidates have similar scores, indicating ambiguity")
