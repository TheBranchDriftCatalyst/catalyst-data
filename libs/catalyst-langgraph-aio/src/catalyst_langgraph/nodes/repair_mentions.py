"""Node: repair mention candidates based on validation errors."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.prompts import load_prompt
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

from catalyst_contracts.models.extraction_output import MentionExtractionResult

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = (
    "Fix the following entity mentions based on the validation errors. "
    "Return a corrected JSON object with a 'mentions' array."
)


def make_repair_mentions(llm_client: LLMClient):
    """Create a repair_mentions node with closed-over LLM client."""

    async def repair_mentions(state: ExtractionState) -> dict[str, Any]:
        try:
            system = load_prompt("mention_repair", FALLBACK_PROMPT)
            raw_text = state.get("raw_text", "")
            candidates = state.get("current_mention_candidates", [])
            validation = state.get("latest_mention_validation", {})
            errors = validation.get("errors", [])

            prompt = (
                f"Errors:\n{json.dumps(errors, indent=2)}\n\n"
                f"Mentions:\n{json.dumps(candidates, indent=2)}\n\n"
                f"Original text:\n{raw_text}"
            )

            result = await llm_client.structured_output(
                MentionExtractionResult,
                [SystemMessage(content=system), HumanMessage(content=prompt)],
            )

            repaired = [m.model_dump() for m in result.mentions]

            retry_count = state.get("mention_retry_count", 0) + 1

            return {
                "current_mention_candidates": repaired,
                "mention_retry_count": retry_count,
                "status": WorkflowStatus.VALIDATING_MENTIONS.value,
                "latest_repair_plan": {
                    "type": "mention_repair",
                    "errors": errors,
                    "retry": retry_count,
                },
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "repair_mentions",
                        "status": "completed",
                        "details": {
                            "retry_count": retry_count,
                            "repaired_count": len(repaired),
                        },
                    }
                ],
            }
        except Exception as e:
            logger.exception("repair_mentions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "repair_mentions",
                        "status": "error",
                        "details": {"error": str(e)},
                    }
                ],
            }

    return repair_mentions
