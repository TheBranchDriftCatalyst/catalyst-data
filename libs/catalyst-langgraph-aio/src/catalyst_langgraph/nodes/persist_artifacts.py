"""Node: persist accepted artifacts via repository."""

from __future__ import annotations

import logging
from typing import Any

from catalyst_langgraph.nodes._audit import make_audit_event
from catalyst_langgraph.repository.base import ArtifactRepository
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)


class PersistArtifacts:
    """Persist accepted artifacts via repository."""

    def __init__(self, repository: ArtifactRepository) -> None:
        self.repository = repository

    async def __call__(self, state: ExtractionState) -> dict[str, Any]:
        try:
            metadata = state.get("source_metadata", {})
            document_id = metadata.get("document_id", "unknown")

            mentions = state.get("accepted_mentions", [])
            propositions = state.get("accepted_propositions", [])
            audit_events = state.get("audit_events", [])

            await self.repository.save_mentions(document_id, mentions)
            await self.repository.save_propositions(document_id, propositions)
            await self.repository.save_audit_trail(document_id, audit_events)

            return {
                "status": WorkflowStatus.COMPLETED.value,
                "audit_events": audit_events
                + [make_audit_event("persist_artifacts", "completed", mentions_saved=len(mentions), propositions_saved=len(propositions))],
            }
        except Exception as e:
            logger.exception("persist_artifacts failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("persist_artifacts", "error", error=str(e))],
            }


# Backward-compatible alias
make_persist_artifacts = PersistArtifacts
