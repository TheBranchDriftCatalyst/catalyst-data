"""Extraction graph state definitions.

ExGraphState is the generic state for composable extraction pipelines.
Unlike ExtractionState (which has hardcoded mention_*/proposition_* fields),
ExGraphState uses a stages dict keyed by stage name.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


class EntityCluster(TypedDict, total=False):
    """A cluster of related entities identified in a document.

    Produced by ClusterEntitiesNode (Phase 2, CD-j6d3).
    """

    cluster_id: str
    """Unique identifier for this cluster."""

    mention_indices: list[int]
    """Indices into stages.ner.accepted for the mentions in this cluster."""

    doc_char_start: int
    """Bounding-box start (doc-char offset) of the cluster."""

    doc_char_end: int
    """Bounding-box end (doc-char offset) of the cluster."""


class EvidenceWindow(TypedDict, total=False):
    """A text window packed around an entity cluster for SPO extraction.

    Produced by PackEvidenceNode (Phase 2, CD-j6d3).
    """

    window_id: str
    """Unique identifier for this evidence window."""

    doc_char_start: int
    """Start offset of the evidence window in the source document."""

    doc_char_end: int
    """End offset of the evidence window in the source document."""

    text: str
    """The evidence window text (may be a sub-string of the full doc)."""

    mention_indices: list[int]
    """Indices into stages.ner.accepted for the mentions in this window."""

    cluster_id: str
    """The cluster whose bounding box seeded this window."""


class ConsensusMention(TypedDict, total=False):
    """A consensus mention produced by ConsensusNode (Phase B, CD-94ow).

    Aggregates per-encoder mentions into a single entry with full provenance
    so downstream nodes (ClusterEntitiesNode, SPO) and the HITL viewer can
    see exactly how each mention was voted on.
    """

    mention_id: str
    """Stable id derived from (canonical_text, canonical_type, span_start).
    MD5-hex[:12] of ``"{canonical_text}|{canonical_type}|{span_start}"``.
    """

    text: str
    """Canonical surface form (lower-stripped from the highest-conf encoder)."""

    canonical_type: str
    """Post-vote canonical MentionType string (e.g. ``"PERSON"``, ``"ORG"``)."""

    span_start: int
    """Character offset start — taken from the highest-confidence encoder."""

    span_end: int
    """Character offset end — taken from the highest-confidence encoder."""

    span_provenance: str
    """Which encoder's span was used (its encoder_name string)."""

    source_models: list[str]
    """Encoders that contributed a mention to this cluster."""

    vote_count: int
    """Number of unique source_models that voted for this mention."""

    n_encoders: int
    """Total encoders in the ensemble (denominator for vote fractions)."""

    mean_confidence: float
    """Mean confidence across all raw mentions in the cluster."""

    type_votes: dict[str, int]
    """canonical_type → number of mentions that voted for it."""

    raw_mentions: list[dict]
    """Per-encoder source mentions preserved for debugging / HITL."""


class ExGraphStatus(StrEnum):
    """Status of the extraction graph execution."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    PERSISTING = "persisting"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageStateDict(TypedDict, total=False):
    """Per-stage state within ExGraphState.stages.

    Each stage (NER, SPO, etc.) has its own isolated state.
    """

    candidates: list[dict[str, Any]]
    """Raw extraction output (unvalidated)."""

    accepted: list[dict[str, Any]]
    """Validated + accepted items."""

    validation: dict[str, Any]
    """Latest MCP validation result (verdict, errors, valid_items)."""

    retry_count: int
    """Number of repair cycles executed."""

    status: str
    """Stage-level status."""

    error: str
    """Error message if stage failed."""


class ExGraphState(TypedDict, total=False):
    """Generic state for composable extraction graphs.

    The key difference from ExtractionState: stages are stored in a dict
    keyed by stage_name, not in hardcoded mention_*/proposition_* fields.
    This enables arbitrary stage composition.

    Usage in LangGraph:
        graph = StateGraph(ExGraphState)
    """

    # ── Input ────────────────────────────────────────────────────────
    raw_text: str
    """Source text to extract from."""

    source_metadata: dict[str, Any]
    """Document/chunk metadata: {document_id, chunk_id, domain, ...}"""

    # ── Run-context attribution (for unified event stream) ───────────
    model: str
    """Model identifier — propagated into every emitted audit event."""

    doc_id: str
    """Document identifier — propagated into every emitted audit event."""

    chunk_idx: int
    """Chunk index within the document — propagated into every event."""

    # ── Chunking ────────────────────────────────────────────────────
    chunks: list[dict[str, Any]]
    """Text chunks produced by ChunkNode (or pre-provided by Dagster asset).
    Each dict has: chunk_id, text, index."""

    # ── Stage results (keyed by stage_name) ──────────────────────────
    stages: dict[str, StageStateDict]
    """Per-stage state. Each key is a stage_name from StageConfig."""

    # ── Cross-stage context ──────────────────────────────────────────
    upstream_context: dict[str, Any]
    """Data from upstream stages (e.g. accepted_mentions for SPO extraction)."""

    # ── Pipeline-level bookkeeping ───────────────────────────────────
    max_retries: int
    """Max repair cycles per stage (can be overridden by StageConfig)."""

    status: str
    """Overall pipeline status (ExGraphStatus)."""

    audit_events: list[dict[str, Any]]
    """Accumulated audit events from all stages."""

    error: str
    """Error message if pipeline failed."""

    # ── Phase 2: Entity-anchored flow (CD-j6d3) ─────────────────────────────
    entity_clusters: list[EntityCluster]
    """Entity clusters produced by ClusterEntitiesNode."""

    evidence_windows: list[EvidenceWindow]
    """Evidence windows produced by PackEvidenceNode."""

    evidence_window_id: str
    """Set when running the SPO sub-graph for one specific evidence window."""

    # ── Phase A: NER ensemble (CD-7h9m) ─────────────────────────────────────
    per_encoder_mentions: dict[str, list[dict[str, Any]]]
    """Per-encoder mention lists from NerEnsembleNode.

    Keyed by encoder name (cfg.model_override).  Each value is the list of
    accepted mentions that encoder produced.  Empty list on timeout / error.
    Populated by NerEnsembleNode; consumed by ConsensusNode (Phase B).
    """

    ensemble_errors: dict[str, dict[str, Any]]
    """Per-encoder error info for encoders that failed or timed out.

    Keyed by encoder name.  Absent when all encoders succeeded.
    Shape: ``{"type": "timeout"|ExceptionClassName, "message"?: str, "duration_s": float}``.
    """

    # ── Phase B: Consensus (CD-94ow) ─────────────────────────────────────────
    consensus_mentions: list[ConsensusMention]
    """Accepted consensus mentions produced by ConsensusNode.

    Each entry is a ConsensusMention with full provenance: vote_count,
    source_models, mean_confidence, type_votes, span_provenance.
    Consumed by ClusterEntitiesNode (falls back to stages.ner.accepted
    for legacy single-NER pipelines when this key is absent).
    """

    rejected_mentions: list[dict[str, Any]]
    """Mentions that did not reach quorum in ConsensusNode.

    Each entry carries: text, canonical_type, vote_count, n_encoders,
    quorum, source_models, raw_mentions.  Persisted for HITL / DPO.
    """
