"""Chunking node — splits raw text into chunks based on model context window.

Uses ChunkConfig from dagster-io to determine chunk size. Runs as the first
node in the extraction pipeline before per-chunk NER/SPO stages.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

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
        # If chunks already provided, passthrough
        existing_chunks = state.get("chunks")
        if existing_chunks:
            logger.info("chunk: %d pre-chunked chunks, passthrough", len(existing_chunks))
            return {}

        raw_text = state.get("raw_text", "")
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

        audit_event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "node_name": "chunk",
            "status": "completed",
            "duration_s": elapsed,
            "details": {
                "chunk_count": len(chunks),
                "target_tokens": self.config.target_tokens,
                "strategy": self.config.strategy,
            },
        }

        return {
            "chunks": chunks,
            "audit_events": state.get("audit_events", []) + [audit_event],
        }
