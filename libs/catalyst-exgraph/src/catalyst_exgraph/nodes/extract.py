"""Generic extraction node — parameterized by StageConfig.

Replaces both ExtractMentions and ExtractPropositions with a single
configurable node that works for any extraction type.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.nodes.spans import correct_candidate_spans
from catalyst_exgraph.protocol import ExtractionClient
from catalyst_exgraph.state import ExGraphState, ExGraphStatus

logger = logging.getLogger(__name__)


def _load_prompt(config: StageConfig) -> str:
    """Load a prompt from config.prompt_dir or fall back to PROMPT_REGISTRY_DIR env var."""
    from pathlib import Path

    # Try config.prompt_dir first (explicit > env var)
    if config.prompt_dir:
        prompt_path = Path(config.prompt_dir) / f"{config.prompt_id}.prompt"
        if prompt_path.is_file():
            from catalyst_langgraph.prompts import parse_prompt_file

            return parse_prompt_file(prompt_path, config.prompt_id).system_content

    # Fall back to env var
    from catalyst_langgraph.prompts import load_prompt

    return load_prompt(config.prompt_id, config.fallback_prompt)


def _make_audit_event(node_name: str, status: str, duration_s: float | None = None, **details) -> dict:
    """Create a standardized audit event."""
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "node_name": node_name,
        "status": status,
        "duration_s": duration_s,
        "details": details,
    }


class ExtractNode:
    """Generic extraction node.

    Calls client.structured_output() with the schema and prompt from StageConfig.
    Populates state["stages"][config.stage_name] with candidates.

    For SPO stages, reads accepted items from upstream stages via
    state["upstream_context"].
    """

    def __init__(self, config: StageConfig, client: ExtractionClient) -> None:
        self.config = config
        self.client = client

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        raw_text = state.get("raw_text", "")
        stage_name = self.config.stage_name
        node_name = f"extract_{stage_name}"

        logger.info("%s: start, input_len=%d", node_name, len(raw_text))
        t0 = time.perf_counter()

        try:
            # Load prompt from config.prompt_dir or PROMPT_REGISTRY_DIR env var
            system = _load_prompt(self.config)

            # Build human message — for SPO stages, include upstream context
            if self.config.stage_name == "spo":
                upstream = state.get("upstream_context", {})
                accepted_mentions = upstream.get("accepted_mentions", [])
                prompt = f"Accepted mentions:\n{json.dumps(accepted_mentions, indent=2)}\n\nText:\n{raw_text}"
            else:
                prompt = raw_text

            result = await self.client.structured_output(
                self.config.extraction_schema,
                [SystemMessage(content=system), HumanMessage(content=prompt)],
            )

            # Extract candidates from the Pydantic result
            # Works for both MentionExtractionResult.mentions and PropositionExtractionResult.propositions
            candidates = []
            for field_name in ("mentions", "propositions"):
                items = getattr(result, field_name, None)
                if items is not None:
                    candidates = [item.model_dump() for item in items]
                    break

            candidates = correct_candidate_spans(candidates, raw_text)

            elapsed = time.perf_counter() - t0
            logger.info("%s: done, candidates=%d, duration=%.3fs", node_name, len(candidates), elapsed)

            # Initialize or update stage state
            stages = dict(state.get("stages", {}))
            stages[stage_name] = {
                "candidates": candidates,
                "accepted": [],
                "validation": {},
                "retry_count": 0,
                "status": "validating",
                "error": "",
            }

            # If no candidates extracted (e.g. encoder returning empty SPO),
            # skip validation and accept empty list
            if not candidates:
                logger.info("%s: 0 candidates, skipping validation", node_name)
                stages[stage_name]["status"] = "completed"
                stages[stage_name]["accepted"] = []
                return {
                    "stages": stages,
                    "status": ExGraphStatus.COMPLETED.value
                    if stage_name == state.get("_final_stage")
                    else state.get("status", ExGraphStatus.EXTRACTING.value),
                    "audit_events": state.get("audit_events", [])
                    + [_make_audit_event(node_name, "completed", elapsed, candidate_count=0, skipped="empty")],
                }

            return {
                "stages": stages,
                "status": ExGraphStatus.VALIDATING.value,
                "audit_events": state.get("audit_events", [])
                + [_make_audit_event(node_name, "completed", elapsed, candidate_count=len(candidates))],
            }
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.exception("%s failed", node_name)
            stages = dict(state.get("stages", {}))
            stages[stage_name] = {
                "candidates": [],
                "accepted": [],
                "validation": {},
                "retry_count": 0,
                "status": "error",
                "error": str(e),
            }
            return {
                "stages": stages,
                "status": ExGraphStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [_make_audit_event(node_name, "error", elapsed, error=str(e))],
            }
