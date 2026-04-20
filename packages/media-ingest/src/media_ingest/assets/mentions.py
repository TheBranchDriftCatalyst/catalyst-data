"""Gold: Mention extraction from media transcription chunks via LLM.

Partitioned by document_id — each run extracts mentions from one document's chunks.
Domain-specific prompts loaded from PROMPT_REGISTRY_DIR (k8s/media-ingest/prompts/).
"""

import time

from dagster import AssetExecutionContext, Output, asset

import dagster_io.extraction as _extraction_mod
from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    Mention,
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
    description="Extract entity mentions from one document's transcription chunks via LLM",
    compute_kind="llm",
    code_version=_CODE_VERSION,
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def media_mentions(
    context: AssetExecutionContext,
    media_chunks: list[TextChunk],
) -> Output[list[Mention]]:
    partition_key = context.partition_key
    context.log.info(
        f"Starting media_mentions extraction for partition={partition_key}, chunk_count={len(media_chunks)}"
    )
    with trace_operation(
        "media_mentions",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
            "chunk_count": len(media_chunks),
        },
    ):
        if not media_chunks:
            context.log.info(f"No chunks for partition={partition_key} — returning empty mentions")
            return Output([], metadata={"mention_count": 0, "document_id": partition_key})

        context.log.info(f"Received {len(media_chunks)} chunks from upstream for LLM extraction")
        llm_start = time.monotonic()
        all_mentions, _ = extract_validated(
            media_chunks,
            code_location="media_ingest",
            max_concurrency=5,
        )
        llm_elapsed = time.monotonic() - llm_start

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_mentions", layer="gold").inc(
            len(all_mentions)
        )
        context.log.info(
            f"LLM extraction complete: {len(all_mentions)} mentions from {len(media_chunks)} chunks "
            f"in {llm_elapsed:.1f}s ({len(all_mentions) / max(len(media_chunks), 1):.1f} mentions/chunk)"
        )
        return Output(
            all_mentions,
            metadata={
                "document_id": partition_key,
                "mention_count": len(all_mentions),
                "chunk_count": len(media_chunks),
            },
        )
