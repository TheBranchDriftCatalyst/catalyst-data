"""State definitions for the extraction workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypedDict


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    EXTRACTING_MENTIONS = "extracting_mentions"
    VALIDATING_MENTIONS = "validating_mentions"
    REPAIRING_MENTIONS = "repairing_mentions"
    EXTRACTING_PROPOSITIONS = "extracting_propositions"
    VALIDATING_PROPOSITIONS = "validating_propositions"
    REPAIRING_PROPOSITIONS = "repairing_propositions"
    PERSISTING = "persisting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SourceMetadata:
    document_id: str
    chunk_id: str
    source: str
    domain: str


@dataclass
class AuditEvent:
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    node_name: str = ""
    status: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class ExtractionState(TypedDict, total=False):
    source_metadata: dict[str, Any]
    raw_text: str

    current_mention_candidates: list[dict[str, Any]]
    current_proposition_candidates: list[dict[str, Any]]

    accepted_mentions: list[dict[str, Any]]
    accepted_propositions: list[dict[str, Any]]

    latest_mention_validation: dict[str, Any]
    latest_proposition_validation: dict[str, Any]
    latest_repair_plan: dict[str, Any]

    mention_retry_count: int
    proposition_retry_count: int
    max_retries: int

    status: str
    audit_events: list[dict[str, Any]]
    error: str
