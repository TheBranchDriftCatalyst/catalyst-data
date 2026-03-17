"""Node: repair mention candidates based on validation errors."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.nodes._audit import make_audit_event
from catalyst_langgraph.prompts import load_prompt
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

from catalyst_contracts.models.extraction_output import MentionExtractionResult

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = (
    "Fix the following entity mentions based on the validation errors. "
    "Return a corrected JSON object with a 'mentions' array."
)


class RepairMentions:
    """Repair mention candidates based on validation errors."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    async def __call__(self, state: ExtractionState) -> dict[str, Any]:
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

            result = await self.llm_client.structured_output(
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
                + [make_audit_event("repair_mentions", "completed", retry_count=retry_count, repaired_count=len(repaired))],
            }
        except Exception as e:
            logger.exception("repair_mentions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("repair_mentions", "error", error=str(e))],
            }


# Backward-compatible alias
make_repair_mentions = RepairMentions
