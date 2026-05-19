"""Gold: Assertion extraction via LangGraph validated pipeline.

Produces structured Assertion objects with qualifiers for leaked documents.
Domain-specific prompts loaded from PROMPT_REGISTRY_DIR (k8s/open-leaks/prompts/).
"""

import time

from dagster import AssetExecutionContext, Output, asset

from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    Assertion,
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
    description="Extract qualified assertions from leak document chunks via LangGraph validated pipeline",
    compute_kind="llm",
    metadata={"layer": "gold"},
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def leak_assertions(
    context: AssetExecutionContext,
    leak_chunks: list[TextChunk],
) -> Output[list[Assertion]]:
    with trace_operation(
        "leak_assertions",
        tracer,
        {
            "code_location": "open_leaks",
            "layer": "gold",
            "chunk_count": len(leak_chunks),
        },
    ):
        if not leak_chunks:
            context.log.info("No chunks — returning empty assertions")
            return Output([], metadata={"assertion_count": 0})

        llm_start = time.monotonic()
        result = extract_validated(
            leak_chunks,
            code_location="open_leaks",
            max_concurrency=5,
        )
        all_assertions = result.assertions
        llm_elapsed = time.monotonic() - llm_start

        negated_count = sum(1 for a in all_assertions if a.negated)
        hedged_count = sum(1 for a in all_assertions if a.hedged)
        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="leak_assertions", layer="gold").inc(
            len(all_assertions)
        )
        context.log.info(
            f"Extracted {len(all_assertions)} assertions from {len(leak_chunks)} chunks "
            f"in {llm_elapsed:.1f}s ({negated_count} negated, {hedged_count} hedged)"
        )
        return Output(
            all_assertions,
            metadata={
                "assertion_count": len(all_assertions),
                "negated_count": negated_count,
                "hedged_count": hedged_count,
            },
        )
