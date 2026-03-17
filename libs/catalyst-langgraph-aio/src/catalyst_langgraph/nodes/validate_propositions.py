"""Node: validate proposition candidates via MCP contract server."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from catalyst_langgraph.clients.mcp import MCPClient
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)


def make_validate_propositions(mcp_client: MCPClient):
    """Create a validate_propositions node with closed-over MCP client."""

    async def validate_propositions(state: ExtractionState) -> dict[str, Any]:
        try:
            candidates = state.get("current_proposition_candidates", [])
            accepted_mentions = state.get("accepted_mentions", [])

            raw_text = state.get("raw_text", "")
            result = await mcp_client.call_tool(
                "validate_propositions",
                {
                    "propositions": candidates,
                    "known_mention_ids": [
                        m.get("surface_form", m.get("text", "")) for m in accepted_mentions
                    ],
                    "source_text": raw_text,
                },
            )

            verdict = result.get("verdict", "invalid")

            update: dict[str, Any] = {
                "latest_proposition_validation": result,
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "validate_propositions",
                        "status": verdict,
                        "details": {
                            "errors": result.get("errors", []),
                        },
                    }
                ],
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
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "validate_propositions",
                        "status": "error",
                        "details": {"error": str(e)},
                    }
                ],
            }

    return validate_propositions
