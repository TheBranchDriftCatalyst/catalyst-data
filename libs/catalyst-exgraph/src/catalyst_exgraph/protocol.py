"""Extraction client protocol and result types.

ExtractionClient is a structural protocol that all extraction adapters
(LLMClient, GLiNERClient, NuExtractClient, UniversalNERClient) satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class ExtractionClient(Protocol):
    """Protocol for extraction clients (LLMs, encoders, etc.).

    All existing clients in catalyst-langgraph-aio already satisfy this
    without modification: LLMClient, GLiNERClient, NuExtractClient,
    UniversalNERClient.
    """

    model: str
    structured_method: str

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel: ...


class StageResult:
    """Result from a single extraction stage.

    Mutable container that the stage graph nodes populate as they execute.
    """

    __slots__ = (
        "candidates",
        "accepted",
        "validation",
        "retry_count",
        "audit_events",
        "status",
        "error",
    )

    def __init__(self) -> None:
        self.candidates: list[dict[str, Any]] = []
        self.accepted: list[dict[str, Any]] = []
        self.validation: dict[str, Any] = {}
        self.retry_count: int = 0
        self.audit_events: list[dict[str, Any]] = []
        self.status: str = "pending"
        self.error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "accepted": self.accepted,
            "validation": self.validation,
            "retry_count": self.retry_count,
            "audit_events": self.audit_events,
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageResult:
        result = cls()
        result.candidates = data.get("candidates", [])
        result.accepted = data.get("accepted", [])
        result.validation = data.get("validation", {})
        result.retry_count = data.get("retry_count", 0)
        result.audit_events = data.get("audit_events", [])
        result.status = data.get("status", "pending")
        result.error = data.get("error", "")
        return result


@dataclass
class ExtractionResult:
    """Container returned by ExtractionResource methods.

    Holds extraction outputs, performance stats, and audit trail.
    """

    mentions: list = field(default_factory=list)
    assertions: list = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    pipeline_breakdown: dict[str, Any] = field(default_factory=dict)
