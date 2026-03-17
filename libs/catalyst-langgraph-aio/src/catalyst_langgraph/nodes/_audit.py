"""Shared audit event helper for graph nodes."""

from __future__ import annotations

from dataclasses import asdict

from catalyst_langgraph.state import AuditEvent


def make_audit_event(node_name: str, status: str, **details) -> dict:
    """Create a standardized audit event dict.

    Uses the AuditEvent dataclass for construction, then converts to dict
    for compatibility with ExtractionState (TypedDict).
    """
    return asdict(AuditEvent(node_name=node_name, status=status, details=details))
