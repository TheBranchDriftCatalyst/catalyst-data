"""Node: extract entity mentions from raw text via LLM."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.prompts import load_prompt, strip_code_fences
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = (
    "Extract all named entity mentions from the following text. "
    "Return a JSON object with a 'mentions' array, where each mention has: "
    "surface_form, entity_type, start_offset, end_offset."
)


def make_extract_mentions(llm_client: LLMClient):
    """Create an extract_mentions node with closed-over LLM client."""

    async def extract_mentions(state: ExtractionState) -> dict[str, Any]:
        try:
            system = load_prompt("mention_extraction", FALLBACK_PROMPT)
            raw_text = state.get("raw_text", "")

            response = await llm_client.complete(raw_text, system=system)

            try:
                parsed = json.loads(strip_code_fences(response))
                candidates = parsed.get("mentions", [])
            except json.JSONDecodeError:
                logger.warning("Failed to parse LLM response as JSON: %s", response[:200])
                candidates = []

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
