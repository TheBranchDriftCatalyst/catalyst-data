"""Node: extract entity mentions from raw text via LLM."""

from __future__ import annotations

import logging
import time
from typing import Any

from catalyst_contracts.models.extraction_output import MentionExtractionResult
from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.nodes._audit import make_audit_event
from catalyst_langgraph.prompts import load_prompt
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = (
    "Extract all named entity mentions from the following text.\n\n"
    "## Output JSON Schema\n"
    '{"mentions": [{"text": "string", "mention_type": "PERSON|ORG|GPE|LOC|DATE|LAW|EVENT|MONEY|NORP|'
    'FACILITY|DOCUMENT|BOOK|ROLE|STRATEGIC_ASSET|FINANCIAL_INSTRUMENT|OTHER", '
    '"span_start": "int (0-based)", "span_end": "int (exclusive)", "confidence": "float 0-1"}]}\n\n'
    "## Example\n"
    'Input: "President Obama signed the Affordable Care Act in March 2010."\n'
    '{"mentions": [\n'
    '  {"text": "President Obama", "mention_type": "PERSON", "span_start": 0, "span_end": 15, "confidence": 1.0},\n'
    '  {"text": "Affordable Care Act", "mention_type": "LAW", "span_start": 27, "span_end": 46, "confidence": 1.0},\n'
    '  {"text": "March 2010", "mention_type": "DATE", "span_start": 50, "span_end": 60, "confidence": 1.0}\n'
    "]}\n\n"
    "Rules: no duplicate spans, no pronouns, committees are ORG not GPE."
)


class ExtractMentions:
    """Extract entity mentions from raw text via LLM."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    async def __call__(self, state: ExtractionState) -> dict[str, Any]:
        raw_text = state.get("raw_text", "")
        logger.info("extract_mentions: start, input_len=%d", len(raw_text))
        t0 = time.perf_counter()
        try:
            system = load_prompt("mention_extraction", FALLBACK_PROMPT)

            result = await self.llm_client.structured_output(
                MentionExtractionResult,
                [SystemMessage(content=system), HumanMessage(content=raw_text)],
            )

            candidates = [m.model_dump() for m in result.mentions]
            logger.debug("extract_mentions: candidates=%d", len(candidates))

            elapsed = time.perf_counter() - t0
            logger.info("extract_mentions: done, candidates=%d, duration=%.3fs", len(candidates), elapsed)
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
