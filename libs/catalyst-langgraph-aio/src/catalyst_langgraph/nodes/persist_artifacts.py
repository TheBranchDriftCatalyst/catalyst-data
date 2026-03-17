"""Node: persist accepted artifacts via repository."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from catalyst_langgraph.repository.base import ArtifactRepository
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)


def make_persist_artifacts(repository: ArtifactRepository):
    """Create a persist_artifacts node with closed-over repository."""

    async def persist_artifacts(state: ExtractionState) -> dict[str, Any]:
        try:
            metadata = state.get("source_metadata", {})
            document_id = metadata.get("document_id", "unknown")

            mentions = state.get("accepted_mentions", [])
            propositions = state.get("accepted_propositions", [])
            audit_events = state.get("audit_events", [])

            await repository.save_mentions(document_id, mentions)
            await repository.save_propositions(document_id, propositions)
            await repository.save_audit_trail(document_id, audit_events)

            return {
                "status": WorkflowStatus.COMPLETED.value,
                "audit_events": audit_events
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "persist_artifacts",
                        "status": "completed",
                        "details": {
                            "mentions_saved": len(mentions),
                            "propositions_saved": len(propositions),
                        },
                    }
                ],
            }
        except Exception as e:
            logger.exception("persist_artifacts failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "persist_artifacts",
                        "status": "error",
                        "details": {"error": str(e)},
                    }
                ],
            }

    return persist_artifacts
