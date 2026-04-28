"""Generic validation node — calls MCP contract validators.

Uses the same validate_mentions/validate_propositions tools from
catalyst-llm-contract-mcp. The validation_tool name comes from StageConfig.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.state import ExGraphState, ExGraphStatus

logger = logging.getLogger(__name__)


class ValidateNode:
    """Generic validation node.

    Calls mcp_client.call_tool(config.validation_tool, ...) with the
    candidates from the current stage. Handles verdict routing:
    - valid: accept all, move to next stage
    - ambiguous: accept valid subset, route to repair
    - invalid: route to repair
    """

    def __init__(self, config: StageConfig, mcp_client: Any) -> None:
        self.config = config
        self.mcp_client = mcp_client

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        stage_name = self.config.stage_name
        node_name = f"validate_{stage_name}"
        stages = dict(state.get("stages", {}))
        stage = dict(stages.get(stage_name, {}))
        candidates = stage.get("candidates", [])

        logger.info("%s: start, candidates=%d", node_name, len(candidates))
        t0 = time.perf_counter()

        try:
            raw_text = state.get("raw_text", "")
            source_metadata = state.get("source_metadata", {})
            document_id = source_metadata.get("document_id", "")

            # Build MCP validation arguments based on stage type
            if self.config.validation_tool == "validate_mentions":
                args = {
                    "mentions": candidates,
                    "source_text": raw_text,
                    "document_id": document_id,
                }
            elif self.config.validation_tool == "validate_propositions":
                # Get accepted mentions from upstream context for known_mention_ids
                upstream = state.get("upstream_context", {})
                accepted_mentions = upstream.get("accepted_mentions", [])
                args = {
                    "propositions": candidates,
                    "known_mention_ids": [m.get("id", "") for m in accepted_mentions if "id" in m],
                    "source_text": raw_text,
                }
            else:
                # Generic fallback
                args = {"items": candidates, "source_text": raw_text}

            result = await self.mcp_client.call_tool(self.config.validation_tool, args)

            verdict = result.get("verdict", "invalid")
            valid_items = result.get("valid_items", [])
            elapsed = time.perf_counter() - t0

            logger.info(
                "%s: verdict=%s, valid=%d, invalid=%d, duration=%.3fs",
                node_name,
                verdict,
                result.get("valid_count", 0),
                result.get("invalid_count", 0),
                elapsed,
            )

            stage["validation"] = result

            if verdict == "valid":
                # All valid — accept everything, assign IDs
                for m in candidates:
                    m["id"] = (
                        f"{m.get('mention_type', m.get('entity_type', 'UNK'))}:"
                        f"{m.get('span_start', 0)}:{m.get('span_end', 0)}"
                    )
                stage["accepted"] = candidates
                stage["status"] = "completed"
            elif verdict == "ambiguous":
                # Partial — accept valid subset
                accepted = [candidates[i] for i in valid_items if i < len(candidates)]
                for m in accepted:
                    m["id"] = (
                        f"{m.get('mention_type', m.get('entity_type', 'UNK'))}:"
                        f"{m.get('span_start', 0)}:{m.get('span_end', 0)}"
                    )
                stage["accepted"] = accepted
                stage["status"] = "repairing"
            else:
                # Invalid — repair needed
                stage["status"] = "repairing"

            stages[stage_name] = stage

            audit = _make_audit_event(
                node_name,
                verdict,
                elapsed,
                valid_count=result.get("valid_count", 0),
                invalid_count=result.get("invalid_count", 0),
                errors=result.get("errors", []),
            )

            return {
                "stages": stages,
                "audit_events": state.get("audit_events", []) + [audit],
            }
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.exception("%s failed", node_name)
            stage["status"] = "error"
            stage["error"] = str(e)
            stages[stage_name] = stage
            return {
                "stages": stages,
                "status": ExGraphStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [_make_audit_event(node_name, "error", elapsed, error=str(e))],
            }


def _make_audit_event(node_name: str, status: str, duration_s: float | None = None, **details) -> dict:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "node_name": node_name,
        "status": status,
        "duration_s": duration_s,
        "details": details,
    }
