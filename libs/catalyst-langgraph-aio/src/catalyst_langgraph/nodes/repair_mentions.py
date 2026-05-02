"""Node: repair mention candidates based on validation errors."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from catalyst_contracts.models.extraction_output import MentionExtractionResult
from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.nodes._audit import make_audit_event
from catalyst_langgraph.nodes._spans import correct_candidate_spans
from catalyst_langgraph.prompts import load_prompt
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

logger = logging.getLogger(__name__)

FALLBACK_PROMPT = (
    "Fix the following entity mentions based on the validation errors. "
    "Return a corrected JSON object with a 'mentions' array."
)


def _find_correct_spans(candidates: list[dict], source_text: str) -> dict[str, list[dict]]:
    """Pre-compute correct span offsets for all mention texts.

    Returns a map of text → [{start, end}] so the LLM gets exact
    offsets instead of guessing.
    """
    span_hints: dict[str, list[dict]] = {}
    for m in candidates:
        text = m.get("text", "")
        if not text or text in span_hints:
            continue
        spans = []
        start = 0
        while True:
            idx = source_text.find(text, start)
            if idx == -1:
                break
            spans.append({"start": idx, "end": idx + len(text)})
            start = idx + 1
        if not spans:
            # Fallback: case-insensitive
            lower_source = source_text.lower()
            needle = text.strip().lower()
            start = 0
            while True:
                idx = lower_source.find(needle, start)
                if idx == -1:
                    break
                spans.append({"start": idx, "end": idx + len(needle)})
                start = idx + 1
        span_hints[text] = spans
    return span_hints


class RepairMentions:
    """Repair mention candidates based on validation errors."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    async def __call__(self, state: ExtractionState) -> dict[str, Any]:
        candidates = state.get("current_mention_candidates", [])
        retry_count = state.get("mention_retry_count", 0) + 1
        logger.info("repair_mentions: start, candidates=%d, retry=%d", len(candidates), retry_count)
        t0 = time.perf_counter()
        try:
            system = load_prompt("mention_repair", FALLBACK_PROMPT)
            raw_text = state.get("raw_text", "")
            validation = state.get("latest_mention_validation", {})
            errors = validation.get("errors", [])
            logger.debug("repair_mentions: errors_to_fix=%d", len(errors))

            # Pre-compute correct spans so the LLM doesn't guess
            span_hints = _find_correct_spans(candidates, raw_text)

            prompt = (
                f"Errors:\n{json.dumps(errors, indent=2)}\n\n"
                f"Mentions:\n{json.dumps(candidates, indent=2)}\n\n"
                f"Correct span offsets (use these for span_start/span_end):\n"
                f"{json.dumps(span_hints, indent=2)}\n\n"
                f"Original text:\n{raw_text}"
            )

            result = await self.llm_client.structured_output(
                MentionExtractionResult,
                [SystemMessage(content=system), HumanMessage(content=prompt)],
            )

            repaired = [m.model_dump() for m in result.mentions]
            repaired = correct_candidate_spans(repaired, raw_text)

            elapsed = time.perf_counter() - t0
            logger.info(
                "repair_mentions: done, repaired=%d, retry=%d, duration=%.3fs", len(repaired), retry_count, elapsed
            )
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
                    make_audit_event(
                        "repair_mentions",
                        "completed",
                        state=state,
                        repaired_count=len(repaired),
                    )
                ],
            }
        except Exception as e:
            logger.exception("repair_mentions failed")
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [make_audit_event("repair_mentions", "error", state=state, error=str(e))],
            }


# Backward-compatible alias
make_repair_mentions = RepairMentions
