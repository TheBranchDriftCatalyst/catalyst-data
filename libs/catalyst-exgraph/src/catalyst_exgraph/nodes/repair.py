"""Generic repair node — sends validation feedback back to LLM for correction.

Parameterized by StageConfig. Reads validation errors, computes correct
span hints, and asks the LLM to fix the extraction.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.nodes.spans import compute_correct_spans, correct_candidate_spans
from catalyst_exgraph.protocol import ExtractionClient
from catalyst_exgraph.state import ExGraphState, ExGraphStatus

logger = logging.getLogger(__name__)


def _load_repair_prompt(config: StageConfig) -> str:
    """Load repair prompt from config.prompt_dir or env var fallback."""
    from pathlib import Path

    if config.prompt_dir:
        prompt_path = Path(config.prompt_dir) / f"{config.repair_prompt_id}.prompt"
        if prompt_path.is_file():
            from catalyst_langgraph.prompts import parse_prompt_file

            return parse_prompt_file(prompt_path, config.repair_prompt_id).system_content

    from catalyst_langgraph.prompts import load_prompt

    return load_prompt(config.repair_prompt_id, config.fallback_repair_prompt)


class RepairNode:
    """Generic repair node.

    Takes validation errors + current candidates, asks the LLM to produce
    a corrected extraction. Pre-computes correct span offsets as hints.
    Increments retry_count.
    """

    def __init__(self, config: StageConfig, client: ExtractionClient) -> None:
        self.config = config
        self.client = client

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        stage_name = self.config.stage_name
        node_name = f"repair_{stage_name}"
        stages = dict(state.get("stages", {}))
        stage = dict(stages.get(stage_name, {}))
        candidates = stage.get("candidates", [])
        validation = stage.get("validation", {})
        raw_text = state.get("raw_text", "")

        retry_count = stage.get("retry_count", 0)
        logger.info("%s: start, retry=%d, candidates=%d", node_name, retry_count, len(candidates))
        t0 = time.perf_counter()

        try:
            system = _load_repair_prompt(self.config)

            # Pre-compute correct span offsets
            span_hints = compute_correct_spans(candidates, raw_text)

            errors = validation.get("errors", [])
            error_summary = json.dumps(errors[:10], indent=2, default=str)

            prompt = (
                f"Validation errors:\n{error_summary}\n\n"
                f"Current candidates:\n{json.dumps(candidates, indent=2)}\n\n"
                f"Correct span hints:\n{json.dumps(span_hints, indent=2)}\n\n"
                f"Source text:\n{raw_text}"
            )

            result = await self.client.structured_output(
                self.config.extraction_schema,
                [SystemMessage(content=system), HumanMessage(content=prompt)],
            )

            # Extract repaired candidates
            repaired = []
            for field_name in ("mentions", "propositions"):
                items = getattr(result, field_name, None)
                if items is not None:
                    repaired = [item.model_dump() for item in items]
                    break

            repaired = correct_candidate_spans(repaired, raw_text)

            elapsed = time.perf_counter() - t0
            logger.info("%s: done, repaired=%d, duration=%.3fs", node_name, len(repaired), elapsed)

            stage["candidates"] = repaired
            stage["retry_count"] = retry_count + 1
            stage["status"] = "validating"
            stages[stage_name] = stage

            return {
                "stages": stages,
                "status": ExGraphStatus.VALIDATING.value,
                "audit_events": state.get("audit_events", [])
                + [
                    _make_audit_event(
                        node_name, "completed", elapsed, repaired_count=len(repaired), retry=retry_count + 1
                    )
                ],
            }
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.exception("%s failed", node_name)
            stage["retry_count"] = retry_count + 1
            stage["status"] = "error"
            stage["error"] = str(e)
            stages[stage_name] = stage
            return {
                "stages": stages,
                "status": ExGraphStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [_make_audit_event(node_name, "error", elapsed, error=str(e), retry=retry_count + 1)],
            }


def _make_audit_event(node_name: str, status: str, duration_s: float | None = None, **details) -> dict:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "node_name": node_name,
        "status": status,
        "duration_s": duration_s,
        "details": details,
    }
