"""Gold: Assertion extraction from media transcription chunks via LangGraph validated pipeline.

Partitioned by document_id — each run extracts assertions from one document's chunks.
Domain-specific prompts loaded from PROMPT_REGISTRY_DIR (k8s/media-ingest/prompts/).
"""

import time

from dagster import AssetExecutionContext, Output, asset

import dagster_io.extraction as _extraction_mod
from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    Assertion,
    TextChunk,
)
from dagster_io.extraction import extract_validated
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.versioning import code_version_from_modules
from media_ingest.partitions import media_partitions

_CODE_VERSION = code_version_from_modules(_extraction_mod)

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="media_ingest",
    description="Extract qualified assertions from one document's transcription chunks via LLM",
    compute_kind="llm",
    code_version=_CODE_VERSION,
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def media_assertions(
    context: AssetExecutionContext,
    media_chunks: list[TextChunk],
) -> Output[list[Assertion]]:
    partition_key = context.partition_key
    context.log.info(
        f"Starting media_assertions extraction for partition={partition_key}, chunk_count={len(media_chunks)}"
    )
    with trace_operation(
        "media_assertions",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
            "chunk_count": len(media_chunks),
        },
    ):
        logger.info(
            "Starting media_assertions extraction for partition=%s (%d chunks)",
            partition_key,
            len(media_chunks),
        )

        if not media_chunks:
            context.log.info(f"No chunks for partition={partition_key} — returning empty assertions")
            return Output(
                [],
                metadata={
                    "document_id": partition_key,
                    "assertion_count": 0,
                    "negated_count": 0,
                    "hedged_count": 0,
                },
            )

        context.log.info(f"Received {len(media_chunks)} chunks from upstream for LLM extraction")
        llm_start = time.monotonic()
        result = extract_validated(
            media_chunks,
            code_location="media_ingest",
            max_concurrency=5,
        )
        all_assertions = result.assertions
        llm_elapsed = time.monotonic() - llm_start

        negated_count = sum(1 for a in all_assertions if a.negated)
        hedged_count = sum(1 for a in all_assertions if a.hedged)
        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_assertions", layer="gold").inc(
            len(all_assertions)
        )
        context.log.info(
            f"LLM extraction complete in {llm_elapsed:.1f}s: {len(all_assertions)} assertions "
            f"from {len(media_chunks)} chunks ({len(all_assertions) / max(len(media_chunks), 1):.1f} assertions/chunk)"
        )
        context.log.info(
            f"Assertion breakdown: negated={negated_count}, hedged={hedged_count}, "
            f"straightforward={len(all_assertions) - negated_count - hedged_count}"
        )
        return Output(
            all_assertions,
            metadata={
                "document_id": partition_key,
                "assertion_count": len(all_assertions),
                "negated_count": negated_count,
                "hedged_count": hedged_count,
                "chunk_count": len(media_chunks),
            },
        )
