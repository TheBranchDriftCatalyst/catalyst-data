"""Node: extract propositions (SPO triples) from text using accepted mentions."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from catalyst_contracts.models.extraction_output import PropositionExtractionResult
from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.nodes._audit import make_audit_event
from catalyst_langgraph.prompts import load_prompt
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = (
    "Extract Subject-Predicate-Object triples from the following text. "
    "Use the provided entity mentions as subjects/objects where possible.\n\n"
    "## Output JSON Schema\n"
    '{"propositions": [{"subject": "string", "predicate": "string", "object": "string", '
    '"confidence": "float 0-1", "evidence": "string"}]}\n\n'
    "## Example\n"
    'Input: "Apple acquired Beats Electronics for $3 billion."\n'
    '{"propositions": [\n'
    '  {"subject": "Apple", "predicate": "acquired", "object": "Beats Electronics", "confidence": 1.0, '
    '"evidence": "Apple acquired Beats Electronics"}\n'
    "]}\n\n"
    "Anti-patterns: no self-referential triples (subject==object), no pronoun subjects, "
    "no vague predicates (is, was, has)."
)


class ExtractPropositions:
    """Extract propositions (SPO triples) from text using accepted mentions."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    async def __call__(self, state: ExtractionState) -> dict[str, Any]:
        raw_text = state.get("raw_text", "")
        accepted_mentions = state.get("accepted_mentions", [])
        logger.info("extract_propositions: start, input_len=%d, mentions=%d", len(raw_text), len(accepted_mentions))
        t0 = time.perf_counter()
        try:
            system = load_prompt("proposition_extraction", FALLBACK_PROMPT)

            prompt = f"Accepted mentions:\n{json.dumps(accepted_mentions, indent=2)}\n\nText:\n{raw_text}"

            result = await self.llm_client.structured_output(
                PropositionExtractionResult,
                [SystemMessage(content=system), HumanMessage(content=prompt)],
            )

            candidates = [p.model_dump() for p in result.propositions]
            logger.debug("extract_propositions: candidates=%d", len(candidates))

            elapsed = time.perf_counter() - t0
            logger.info("extract_propositions: done, candidates=%d, duration=%.3fs", len(candidates), elapsed)
            return {
                "current_proposition_candidates": candidates,
                "status": WorkflowStatus.VALIDATING_PROPOSITIONS.value,
                "audit_events": state.get("audit_events", [])
                + [
                    make_audit_event(
                        "extract_propositions",
                        "completed",
                        candidate_count=len(candidates),
                    )
                ],
            }
        except Exception as e:
            logger.exception("extract_propositions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("extract_propositions", "error", error=str(e))],
            }


# Backward-compatible alias
make_extract_propositions = ExtractPropositions
