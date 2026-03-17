"""Node: validate mention candidates via MCP contract server."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from catalyst_langgraph.clients.mcp import MCPClient
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)


def make_validate_mentions(mcp_client: MCPClient):
    """Create a validate_mentions node with closed-over MCP client."""

    async def validate_mentions(state: ExtractionState) -> dict[str, Any]:
        try:
            candidates = state.get("current_mention_candidates", [])

            raw_text = state.get("raw_text", "")
            document_id = state.get("source_metadata", {}).get("document_id", "")
            result = await mcp_client.call_tool(
                "validate_mentions",
                {
                    "mentions": candidates,
                    "source_text": raw_text,
                    "document_id": document_id,
                },
            )

            verdict = result.get("verdict", "invalid")

            update: dict[str, Any] = {
                "latest_mention_validation": result,
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "validate_mentions",
                        "status": verdict,
                        "details": {
                            "errors": result.get("errors", []),
                        },
                    }
                ],
            }

            if verdict == "valid":
                # Assign stable span-based composite IDs to each accepted mention
                for m in candidates:
                    m["id"] = (
                        f"{m.get('mention_type', m.get('entity_type', 'UNK'))}:"
                        f"{m.get('span_start', m.get('start_offset', 0))}:"
                        f"{m.get('span_end', m.get('end_offset', 0))}"
                    )
                update["accepted_mentions"] = candidates
                update["status"] = WorkflowStatus.EXTRACTING_PROPOSITIONS.value
            else:
                update["status"] = WorkflowStatus.REPAIRING_MENTIONS.value

            return update
        except Exception as e:
            logger.exception("validate_mentions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "validate_mentions",
                        "status": "error",
                        "details": {"error": str(e)},
                    }
                ],
            }

    return validate_mentions
