"""Node: repair proposition candidates based on validation errors."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.nodes._audit import make_audit_event
from catalyst_langgraph.prompts import load_prompt
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

from catalyst_contracts.models.extraction_output import PropositionExtractionResult

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = (
    "Fix the following propositions based on the validation errors. "
    "Ensure all entity references match accepted mentions. "
    "Return a corrected JSON object with a 'propositions' array."
)


class RepairPropositions:
    """Repair proposition candidates based on validation errors."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    async def __call__(self, state: ExtractionState) -> dict[str, Any]:
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

            result = await self.llm_client.structured_output(
                PropositionExtractionResult,
                [SystemMessage(content=system), HumanMessage(content=prompt)],
            )

            repaired = [p.model_dump() for p in result.propositions]

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
                + [make_audit_event("repair_propositions", "completed", retry_count=retry_count, repaired_count=len(repaired))],
            }
        except Exception as e:
            logger.exception("repair_propositions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("repair_propositions", "error", error=str(e))],
            }


# Backward-compatible alias
make_repair_propositions = RepairPropositions
