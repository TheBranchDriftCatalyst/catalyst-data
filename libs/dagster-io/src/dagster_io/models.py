"""EDC core models — Extract, Define, Canonicalize.

Wave 1 (bead ``llm-g0b``) unified ``Mention`` and ``Assertion`` into
``catalyst-contracts-core``. The shapes in this module are now re-exports
of the canonical contracts-core types. ``EntityCandidate``,
``CanonicalEntity``, ``AlignmentEdge``, ``SpeakerEmbedding``, and
``SpeakerProfile`` remain catalyst-data-side persisted models (not
extraction outputs) and stay local.

Re-exports from catalyst-contracts-core:
    - Provenance
    - Mention   (AMR-aware + entity-link-aware)
    - Assertion (AMR-aware + entity-link-aware)
    - MentionType, AlignmentType, ExtractionMethod (enums)

Local catalyst-data models (not extraction outputs):
    - EntityCandidate    : grouped mentions within a code location
    - CanonicalEntity    : cross-source resolved entity (platinum layer)
    - AlignmentEdge      : cross-source entity alignment record
    - SpeakerEmbedding   : per-speaker centroid from one partition
    - SpeakerProfile     : cross-file speaker identity
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

# Canonical source: catalyst-contracts-core. Re-exported here so existing
# call sites ``from dagster_io.models import Assertion, Mention, Provenance``
# keep resolving — they now point at the unified contracts-core types.
from catalyst_contracts_core import (
    AlignmentType,
    Assertion,
    ExtractionMethod,
    Mention,
    MentionType,
    Provenance,
)


def _deterministic_id(*parts: str) -> str:
    """SHA-256 hash of concatenated parts, truncated to 16 hex chars."""
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _content_hash(*parts: str) -> str:
    """Full SHA-256 hash for content dedup."""
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()


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
    profile_id: str | None = Field(default=None, description="Speaker profile ID from voice clustering")
    source_documents: list[str] = Field(default_factory=list)
    code_location: str = ""
    content_hash: str = Field(default="", description="For dedup")

    @model_validator(mode="after")
    def _compute_ids(self) -> EntityCandidate:
        if not self.candidate_id:
            self.candidate_id = _deterministic_id(self.canonical_name, self.candidate_type.value, self.code_location)
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
    first_seen: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_seen: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


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
            self.edge_id = _deterministic_id(ids[0], ids[1], self.alignment_type.value)
        return self


class SpeakerEmbedding(BaseModel):
    """Per-speaker centroid embedding from one document partition."""

    partition_key: str
    local_label: str  # pyannote's SPEAKER_XX
    centroid: list[float]  # 192-d vector
    segment_count: int
    total_duration_s: float


class SpeakerProfile(BaseModel):
    """Cross-file speaker identity — one profile per real-world voice."""

    profile_id: str  # sha1(centroid_bytes + first_seen_iso)[:16]
    centroid: list[float]  # 192-d averaged centroid
    display_name: str | None = None
    member_count: int = 0
    total_duration_s: float = 0.0
    first_seen: str  # ISO timestamp
    last_seen: str  # ISO timestamp
    members: list[dict] = Field(default_factory=list)  # [{document_id, local_label, segment_count}]


__all__ = [
    # Re-exported from catalyst-contracts-core (Wave 1 unified types)
    "AlignmentType",
    "Assertion",
    "ExtractionMethod",
    "Mention",
    "MentionType",
    "Provenance",
    # Local catalyst-data persisted models
    "AlignmentEdge",
    "CanonicalEntity",
    "EntityCandidate",
    "SpeakerEmbedding",
    "SpeakerProfile",
]
