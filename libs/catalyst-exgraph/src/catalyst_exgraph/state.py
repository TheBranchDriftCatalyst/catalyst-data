"""Extraction graph state definitions.

ExGraphState is the generic state for composable extraction pipelines.
Unlike ExtractionState (which has hardcoded mention_*/proposition_* fields),
ExGraphState uses a stages dict keyed by stage name.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict


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
