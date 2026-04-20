"""Gold: Mention extraction via LangGraph validated pipeline.

Produces structured Mention objects with span offsets for leaked documents.
Domain-specific prompts loaded from PROMPT_REGISTRY_DIR (k8s/open-leaks/prompts/).
"""

import time

from dagster import AssetExecutionContext, Output, asset

from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    Mention,
    TextChunk,
)
from dagster_io.extraction import extract_validated
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="leaks",
    description="Extract entity mentions from leak document chunks via LangGraph validated pipeline",
    compute_kind="llm",
    metadata={"layer": "gold"},
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def leak_mentions(
    context: AssetExecutionContext,
    leak_chunks: list[TextChunk],
) -> Output[list[Mention]]:
    with trace_operation(
        "leak_mentions",
        tracer,
        {
            "code_location": "open_leaks",
            "layer": "gold",
            "chunk_count": len(leak_chunks),
        },
    ):
        if not leak_chunks:
            context.log.info("No chunks — returning empty mentions")
            return Output([], metadata={"mention_count": 0})

        llm_start = time.monotonic()
        all_mentions, _ = extract_validated(
            leak_chunks,
            code_location="open_leaks",
            max_concurrency=5,
        )
        llm_elapsed = time.monotonic() - llm_start

        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="leak_mentions", layer="gold").inc(
            len(all_mentions)
        )
        context.log.info(f"Extracted {len(all_mentions)} mentions from {len(leak_chunks)} chunks in {llm_elapsed:.1f}s")
        return Output(all_mentions, metadata={"mention_count": len(all_mentions)})
