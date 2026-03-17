"""Node: repair proposition candidates based on validation errors."""

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
    "Fix the following propositions based on the validation errors. "
    "Ensure all entity references match accepted mentions. "
    "Return a corrected JSON object with a 'propositions' array."
)


def make_repair_propositions(llm_client: LLMClient):
    """Create a repair_propositions node with closed-over LLM client."""

    async def repair_propositions(state: ExtractionState) -> dict[str, Any]:
        try:
            system = load_prompt("proposition_repair", FALLBACK_PROMPT)
            raw_text = state.get("raw_text", "")
            candidates = state.get("current_proposition_candidates", [])
            accepted_mentions = state.get("accepted_mentions", [])
            validation = state.get("latest_proposition_validation", {})
            errors = validation.get("errors", [])

            prompt = (
                f"Errors:\n{json.dumps(errors, indent=2)}\n\n"
                f"Propositions:\n{json.dumps(candidates, indent=2)}\n\n"
                f"Accepted mentions:\n{json.dumps(accepted_mentions, indent=2)}\n\n"
                f"Original text:\n{raw_text}"
            )

            response = await llm_client.complete(prompt, system=system)

            try:
                parsed = json.loads(strip_code_fences(response))
                repaired = parsed.get("propositions", [])
            except json.JSONDecodeError:
                logger.warning("repair_propositions: Failed to parse LLM repair response")
                repaired = []

            retry_count = state.get("proposition_retry_count", 0) + 1

            return {
                "current_proposition_candidates": repaired,
                "proposition_retry_count": retry_count,
                "status": WorkflowStatus.VALIDATING_PROPOSITIONS.value,
                "latest_repair_plan": {
                    "type": "proposition_repair",
                    "errors": errors,
                    "retry": retry_count,
                },
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "repair_propositions",
                        "status": "completed",
                        "details": {
                            "retry_count": retry_count,
                            "repaired_count": len(repaired),
                        },
                    }
                ],
            }
        except Exception as e:
            logger.exception("repair_propositions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "node_name": "repair_propositions",
                        "status": "error",
                        "details": {"error": str(e)},
                    }
                ],
            }

    return repair_propositions
