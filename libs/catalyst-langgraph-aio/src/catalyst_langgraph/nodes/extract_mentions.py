"""Node: extract entity mentions from raw text via LLM."""

from __future__ import annotations

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
    "Extract all named entity mentions from the following text. "
    "Return a JSON object with a 'mentions' array, where each mention has: "
    "text, mention_type, span_start, span_end."
)


def make_extract_mentions(llm_client: LLMClient):
    """Create an extract_mentions node with closed-over LLM client."""

    async def extract_mentions(state: ExtractionState) -> dict[str, Any]:
        try:
            system = load_prompt("mention_extraction", FALLBACK_PROMPT)
            raw_text = state.get("raw_text", "")

            result = await llm_client.structured_output(
                MentionExtractionResult,
                [SystemMessage(content=system), HumanMessage(content=raw_text)],
            )

            candidates = [m.model_dump() for m in result.mentions]

            return {
                "current_mention_candidates": candidates,
                "status": WorkflowStatus.VALIDATING_MENTIONS.value,
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "extract_mentions",
                        "status": "completed",
                        "details": {"candidate_count": len(candidates)},
                    }
                ],
            }
        except Exception as e:
            logger.exception("extract_mentions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "extract_mentions",
                        "status": "error",
                        "details": {"error": str(e)},
                    }
                ],
            }

    return extract_mentions
