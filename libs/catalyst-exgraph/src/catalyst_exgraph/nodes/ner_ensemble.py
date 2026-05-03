"""NerEnsembleNode — parallel NER encoder execution with per-encoder audit events.

Phase A of the v4 NER-ensemble extraction epic (CD-7h9m / CD-y4u0).

Runs N encoder models in parallel against a single doc via ``asyncio.gather``.
Each encoder gets its own sub-state with chunk_id ``{doc_id}:_ner_{encoder_name}``
so the State Inspector surfaces one card per (doc, encoder).

Per-encoder timeouts isolate failures: a wedged Ollama-backed encoder can't
stall the whole run — its slot in ``per_encoder_mentions`` becomes ``[]`` and
an ``ner_encoder_completed`` error event is emitted so the anomaly is visible.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.protocol import ExtractionClient
from catalyst_exgraph.state import ExGraphState
from dagster_io import event_tail

logger = logging.getLogger(__name__)


class NerEnsembleNode:
    """Parallel ensemble of NER encoders.

    Each encoder runs against the same doc text via ``asyncio.gather``.
    Per-encoder mention lists are preserved in ``state['per_encoder_mentions']``
    for downstream consensus (Phase B / CD-y4u0).

    Audit events are emitted per (doc, encoder) with the stable chunk_id pattern
    ``{doc_id}:_ner_{encoder_name}`` so the State Inspector renders one rail card
    per encoder — independently of the consensus card that Phase B will add.

    Args:
        encoders: List of StageConfig objects — one per encoder model.
            ``cfg.model_override`` is used as the encoder name (key).
        clients: Pre-resolved clients keyed by encoder name
            (same string as ``cfg.model_override``).
        mcp_client: MCP contract validation client.  Passed to ExtractNode but
            not used for validation in ensemble mode (max_retries=0 encoders
            skip repair).
        per_encoder_timeout_s: Per-encoder wall-clock timeout.  An encoder that
            exceeds this is cancelled; its slot becomes ``[]``.  Default 60 s.
    """

    def __init__(
        self,
        encoders: list[StageConfig],
        clients: dict[str, ExtractionClient],
        mcp_client: Any,
        per_encoder_timeout_s: float = 60.0,
    ) -> None:
        self.encoders = encoders
        self.clients = clients
        self.mcp_client = mcp_client
        self.per_encoder_timeout_s = per_encoder_timeout_s

        # Build one ExtractNode per encoder — lazy import to avoid circular deps
        from catalyst_exgraph.nodes.extract import ExtractNode

        self._nodes: dict[str, ExtractNode] = {
            cfg.model_override or cfg.stage_name: ExtractNode(cfg, clients[cfg.model_override or cfg.stage_name])
            for cfg in encoders
        }

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        src = state.get("source_metadata") or {}
        doc_id = state.get("doc_id") or src.get("document_id") or ""

        async def _run_one(
            encoder_name: str,
            node: Any,
        ) -> tuple[str, list[dict], dict | None]:
            """Run a single encoder, emit lifecycle events, return (name, mentions, err)."""
            chunk_id_for_encoder = f"{doc_id}:_ner_{encoder_name}"

            # Clone parent state; override chunk_id and model so all events from
            # ExtractNode are tagged with this encoder's identity.
            sub_state: ExGraphState = {
                **state,
                "model": encoder_name,
                "chunk_id": chunk_id_for_encoder,
                "source_metadata": {
                    **src,
                    "chunk_id": chunk_id_for_encoder,
                    "encoder_name": encoder_name,
                },
                "stages": {},  # fresh per-encoder stage state
            }

            t0 = time.perf_counter()
            event_tail.append(
                source="harness",
                node_name="ner_encoder_started",
                status="started",
                model=encoder_name,
                doc_id=doc_id,
                chunk_id=chunk_id_for_encoder,
                details={"encoder": encoder_name},
            )

            try:
                result = await asyncio.wait_for(
                    node(sub_state),
                    timeout=self.per_encoder_timeout_s,
                )
                # ExtractNode populates stages[stage_name]["accepted"] after
                # validation — but in encoder mode (max_retries=0) the stage
                # is typically completed with candidates only (no repair loop).
                # Accept whichever list is non-empty: accepted > candidates.
                stage_data = (result.get("stages") or {}).get(node.config.stage_name, {})
                accepted: list[dict] = stage_data.get("accepted") or stage_data.get("candidates") or []

                # Tag each mention with which encoder found it — useful for
                # Phase B consensus to track provenance without losing the list.
                for m in accepted:
                    m["_source_encoder"] = encoder_name

                duration = time.perf_counter() - t0
                event_tail.append(
                    source="harness",
                    node_name="ner_encoder_completed",
                    status="completed",
                    model=encoder_name,
                    doc_id=doc_id,
                    chunk_id=chunk_id_for_encoder,
                    details={
                        "encoder": encoder_name,
                        "mention_count": len(accepted),
                        "duration_s": duration,
                    },
                )
                # Emit chunk_extracted so the State Inspector OUTPUT pane
                # surfaces per-encoder mention counts for v4 chunk_ids.
                # This is the v4 equivalent of emit_chunk_extracted_for_state
                # (which only fires on the legacy build_ner_pipeline path).
                event_tail.emit_chunk_extracted(
                    chunk_id_for_encoder,
                    model=encoder_name,
                    doc_id=doc_id,
                    mentions=accepted,
                    propositions=[],
                )
                logger.info(
                    "ner_ensemble: encoder=%s completed, mentions=%d, duration=%.2fs",
                    encoder_name,
                    len(accepted),
                    duration,
                )
                return encoder_name, accepted, None

            except TimeoutError:
                duration = time.perf_counter() - t0
                logger.warning(
                    "ner_ensemble: encoder=%s timed out after %.1fs",
                    encoder_name,
                    duration,
                )
                event_tail.append(
                    source="harness",
                    node_name="ner_encoder_completed",
                    status="error",
                    model=encoder_name,
                    doc_id=doc_id,
                    chunk_id=chunk_id_for_encoder,
                    details={
                        "encoder": encoder_name,
                        "error": "timeout",
                        "duration_s": duration,
                        "timeout_s": self.per_encoder_timeout_s,
                    },
                )
                return encoder_name, [], {"type": "timeout", "duration_s": duration}

            except Exception as exc:
                duration = time.perf_counter() - t0
                logger.exception("ner_ensemble: encoder=%s raised", encoder_name)
                event_tail.append(
                    source="harness",
                    node_name="ner_encoder_completed",
                    status="error",
                    model=encoder_name,
                    doc_id=doc_id,
                    chunk_id=chunk_id_for_encoder,
                    details={
                        "encoder": encoder_name,
                        "error": type(exc).__name__,
                        "message": str(exc)[:500],
                        "duration_s": duration,
                    },
                )
                return encoder_name, [], {"type": type(exc).__name__, "message": str(exc)[:500]}

        # Launch all encoders in parallel — exceptions are caught inside _run_one
        # so return_exceptions=False is safe: we never let exceptions propagate.
        results = await asyncio.gather(
            *[_run_one(name, node) for name, node in self._nodes.items()],
            return_exceptions=False,
        )

        per_encoder_mentions: dict[str, list[dict]] = {}
        ensemble_errors: dict[str, dict] = {}
        for encoder_name, mentions, err in results:
            per_encoder_mentions[encoder_name] = mentions
            if err:
                ensemble_errors[encoder_name] = err

        logger.info(
            "ner_ensemble: done, encoders=%d, errors=%d, total_mentions=%d",
            len(per_encoder_mentions),
            len(ensemble_errors),
            sum(len(v) for v in per_encoder_mentions.values()),
        )

        return {
            "per_encoder_mentions": per_encoder_mentions,
            "ensemble_errors": ensemble_errors,
        }
