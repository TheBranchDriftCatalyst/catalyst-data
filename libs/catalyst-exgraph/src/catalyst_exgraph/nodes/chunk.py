"""Chunking node — splits raw text into chunks based on model context window.

Uses ChunkConfig from dagster-io to determine chunk size. Runs as the first
node in the extraction pipeline before per-chunk NER/SPO stages.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from catalyst_exgraph.nodes._audit import make_audit_event
from dagster_io.chunking import ChunkConfig, chunk_text

logger = logging.getLogger(__name__)


class ChunkNode:
    """Split raw_text into chunks using ChunkConfig.

    When chunks already exist in state (pre-chunked by Dagster asset),
    this node is a passthrough.
    """

    def __init__(self, config: ChunkConfig):
        self.config = config

    async def __call__(self, state: dict) -> dict[str, Any]:
        raw_text = state.get("raw_text", "")
        target_chars = self.config.target_chars

        # If chunks already provided AND they fit the model's context, passthrough
        existing_chunks = state.get("chunks")
        if existing_chunks:
            oversized = [c for c in existing_chunks if len(c.get("text", "")) > target_chars * 1.5]
            if not oversized:
                logger.info("chunk: %d pre-chunked chunks fit model context, passthrough", len(existing_chunks))
                return {}
            # Re-chunk oversized chunks for this model's context window
            logger.info(
                "chunk: %d/%d chunks exceed target (%d chars), re-chunking",
                len(oversized),
                len(existing_chunks),
                target_chars,
            )
            raw_text = "\n\n".join(c.get("text", "") for c in existing_chunks)

        if not raw_text:
            return {"chunks": []}

        t0 = time.perf_counter()
        text_chunks = chunk_text(raw_text, config=self.config)
        elapsed = time.perf_counter() - t0

        chunks = [{"chunk_id": f"chunk-{i:03d}", "text": tc, "index": i} for i, tc in enumerate(text_chunks)]

        logger.info(
            "chunk: split into %d chunks (%.2fs), target=%d tokens",
            len(chunks),
            elapsed,
            self.config.target_tokens,
        )

        # Route through make_audit_event so the State Inspector picks the
        # event up via event_store — previously this node only appended
        # to state["audit_events"] (post-hoc trail) and the UI saw nothing.
        audit_event = make_audit_event(
            "chunk",
            "completed",
            state=state,
            duration_s=elapsed,
            chunk_count=len(chunks),
            target_tokens=self.config.target_tokens,
            strategy=self.config.strategy,
        )

        return {
            "chunks": chunks,
            "audit_events": state.get("audit_events", []) + [audit_event],
        }
