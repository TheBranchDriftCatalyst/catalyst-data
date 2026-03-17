"""Node: extract entity mentions from raw text via LLM."""

from __future__ import annotations

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
    "Extract all named entity mentions from the following text. "
    "Return a JSON object with a 'mentions' array, where each mention has: "
    "text, mention_type, span_start, span_end."
)


class ExtractMentions:
    """Extract entity mentions from raw text via LLM."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    async def __call__(self, state: ExtractionState) -> dict[str, Any]:
        try:
            system = load_prompt("mention_extraction", FALLBACK_PROMPT)
            raw_text = state.get("raw_text", "")

            result = await self.llm_client.structured_output(
                MentionExtractionResult,
                [SystemMessage(content=system), HumanMessage(content=raw_text)],
            )

            candidates = [m.model_dump() for m in result.mentions]

            return {
                "current_mention_candidates": candidates,
                "status": WorkflowStatus.VALIDATING_MENTIONS.value,
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("extract_mentions", "completed", candidate_count=len(candidates))],
            }
        except Exception as e:
            logger.exception("extract_mentions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("extract_mentions", "error", error=str(e))],
            }


# Backward-compatible alias
make_extract_mentions = ExtractMentions
