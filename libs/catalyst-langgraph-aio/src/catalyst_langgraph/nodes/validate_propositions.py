"""Node: validate proposition candidates via MCP contract server."""

from __future__ import annotations

import logging
from typing import Any

from catalyst_langgraph.clients.mcp import MCPClient
from catalyst_langgraph.nodes._audit import make_audit_event
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)


class ValidatePropositions:
    """Validate proposition candidates via MCP contract server."""

    def __init__(self, mcp_client: MCPClient) -> None:
        self.mcp_client = mcp_client

    async def __call__(self, state: ExtractionState) -> dict[str, Any]:
        try:
            candidates = state.get("current_proposition_candidates", [])
            accepted_mentions = state.get("accepted_mentions", [])

            raw_text = state.get("raw_text", "")
            result = await self.mcp_client.call_tool(
                "validate_propositions",
                {
                    "propositions": candidates,
                    "known_mention_ids": [
                        m["id"] for m in accepted_mentions if "id" in m
                    ],
                    "source_text": raw_text,
                },
            )

            verdict = result.get("verdict", "invalid")

            update: dict[str, Any] = {
                "latest_proposition_validation": result,
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("validate_propositions", verdict, errors=result.get("errors", []))],
            }

            if verdict == "valid":
                update["accepted_propositions"] = candidates
                update["status"] = WorkflowStatus.PERSISTING.value
            else:
                update["status"] = WorkflowStatus.REPAIRING_PROPOSITIONS.value

            return update
        except Exception as e:
            logger.exception("validate_propositions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("validate_propositions", "error", error=str(e))],
            }


# Backward-compatible alias
make_validate_propositions = ValidatePropositions
