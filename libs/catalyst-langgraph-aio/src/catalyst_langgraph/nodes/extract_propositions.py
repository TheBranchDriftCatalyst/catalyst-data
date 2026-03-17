"""Node: extract propositions (SPO triples) from text using accepted mentions."""

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
    "Extract Subject-Predicate-Object triples from the following text. "
    "Use the provided entity mentions as subjects/objects where possible. "
    "Return a JSON object with a 'propositions' array."
)


def make_extract_propositions(llm_client: LLMClient):
    """Create an extract_propositions node with closed-over LLM client."""

    async def extract_propositions(state: ExtractionState) -> dict[str, Any]:
        try:
            system = load_prompt("proposition_extraction", FALLBACK_PROMPT)
            raw_text = state.get("raw_text", "")
            accepted_mentions = state.get("accepted_mentions", [])

            prompt = (
                f"Accepted mentions:\n{json.dumps(accepted_mentions, indent=2)}\n\n"
                f"Text:\n{raw_text}"
            )

            response = await llm_client.complete(prompt, system=system)

            try:
                parsed = json.loads(strip_code_fences(response))
                candidates = parsed.get("propositions", [])
            except json.JSONDecodeError:
                logger.warning("Failed to parse LLM response as JSON: %s", response[:200])
                candidates = []

            return {
                "current_proposition_candidates": candidates,
                "status": WorkflowStatus.VALIDATING_PROPOSITIONS.value,
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "extract_propositions",
                        "status": "completed",
                        "details": {"candidate_count": len(candidates)},
                    }
                ],
            }
        except Exception as e:
            logger.exception("extract_propositions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "extract_propositions",
                        "status": "error",
                        "details": {"error": str(e)},
                    }
                ],
            }

    return extract_propositions
