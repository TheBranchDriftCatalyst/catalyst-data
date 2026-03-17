"""EDC core models — Extract, Define, Canonicalize.

Shared Pydantic models for the knowledge graph pipeline:
- Provenance: extraction lineage tracking
- Mention: entity span extracted from text
- EntityCandidate: grouped mentions within a code location
- CanonicalEntity: cross-source resolved entity
- Assertion: qualified S-P-O triple with provenance
- AlignmentEdge: cross-source entity alignment record
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator

# Canonical source: catalyst-contracts-core — re-exported for backward compat
from catalyst_contracts_core.enums import AlignmentType, ExtractionMethod, MentionType
from catalyst_contracts_core.types import Provenance


def _deterministic_id(*parts: str) -> str:
    """SHA-256 hash of concatenated parts, truncated to 16 hex chars."""
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _content_hash(*parts: str) -> str:
    """Full SHA-256 hash for content dedup."""
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()


class Mention(BaseModel):
    """A single entity mention extracted from a text chunk."""

    mention_id: str = Field(default="", description="Deterministic hash of key fields")
    document_id: str
    chunk_id: str
    text: str
    mention_type: MentionType
    span_start: int | None = None
    span_end: int | None = None
    context: str = ""
    provenance: Provenance | None = None
    content_hash: str = Field(default="", description="For dedup")

    @model_validator(mode="after")
    def _compute_ids(self) -> Mention:
        if not self.mention_id:
            self.mention_id = _deterministic_id(
                self.document_id, self.chunk_id, self.text, self.mention_type.value
            )
        if not self.content_hash:
            self.content_hash = _content_hash(
                self.document_id, self.chunk_id, self.text, self.mention_type.value
            )
        return self


class EntityCandidate(BaseModel):
    """Grouped mentions resolved within a single code location."""

    candidate_id: str = Field(default="", description="Deterministic hash")
    canonical_name: str
    candidate_type: MentionType
    aliases: list[str] = Field(default_factory=list)
    mention_ids: list[str] = Field(default_factory=list)
    mention_count: int = 0
    external_ids: dict[str, str] = Field(default_factory=dict)
    embedding: list[float] | None = None
    source_documents: list[str] = Field(default_factory=list)
    code_location: str = ""
    content_hash: str = Field(default="", description="For dedup")

    @model_validator(mode="after")
    def _compute_ids(self) -> EntityCandidate:
        if not self.candidate_id:
            self.candidate_id = _deterministic_id(
                self.canonical_name, self.candidate_type.value, self.code_location
            )
        if not self.content_hash:
            self.content_hash = _content_hash(
                self.canonical_name,
                self.candidate_type.value,
                self.code_location,
                ",".join(sorted(self.aliases)),
            )
        return self


class CanonicalEntity(BaseModel):
    """Cross-source resolved entity in the platinum layer."""

    canonical_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID-based stable ID",
    )
    canonical_name: str
    entity_type: MentionType
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    external_ids: dict[str, str] = Field(default_factory=dict)
    source_candidate_ids: list[str] = Field(default_factory=list)
    source_code_locations: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None
    mention_count: int = 0
    first_seen: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Assertion(BaseModel):
    """A qualified S-P-O triple with provenance."""

    assertion_id: str = Field(default="", description="Deterministic hash")
    subject_text: str
    subject_mention_id: str = ""
    predicate: str
    predicate_canonical: str = ""
    object_text: str
    object_mention_id: str = ""
    qualifiers: dict[str, Any] = Field(
        default_factory=dict,
        description="Keys: time, location, condition, manner, source_attribution",
    )
    confidence: float = Field(default=1.0, ge=0, le=1)
    provenance: Provenance | None = None
    negated: bool = False
    hedged: bool = False
    content_hash: str = Field(default="", description="For dedup")

    @model_validator(mode="after")
    def _compute_ids(self) -> Assertion:
        if not self.assertion_id:
            self.assertion_id = _deterministic_id(
                self.subject_text, self.predicate, self.object_text,
                self.provenance.chunk_id if self.provenance else "",
            )
        if not self.content_hash:
            self.content_hash = _content_hash(
                self.subject_text, self.predicate, self.object_text,
                str(self.negated), str(self.hedged),
            )
        return self


class AlignmentEdge(BaseModel):
    """Cross-source entity alignment record."""

    edge_id: str = Field(default="", description="Deterministic hash")
    source_entity_id: str
    target_entity_id: str
    alignment_type: AlignmentType
    score: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    method: str = ""

    @model_validator(mode="after")
    def _compute_ids(self) -> AlignmentEdge:
        if not self.edge_id:
            ids = sorted([self.source_entity_id, self.target_entity_id])
            self.edge_id = _deterministic_id(
                ids[0], ids[1], self.alignment_type.value
            )
        return self
