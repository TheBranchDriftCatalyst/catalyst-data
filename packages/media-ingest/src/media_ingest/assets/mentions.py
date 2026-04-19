"""Gold: Mention extraction from media transcription chunks via LLM.

Partitioned by document_id — each run extracts mentions from one document's chunks.
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
from dagster_io.prompts import load_prompt
from dagster_io.versioning import code_version_from_modules
from media_ingest.partitions import media_partitions

_CODE_VERSION = code_version_from_modules(_extraction_mod)

logger = get_logger(__name__)
tracer = get_tracer(__name__)

MENTION_SYSTEM_PROMPT = load_prompt(
    "mentions/media",
    fallback="""\
You are a named-entity extraction system specialized in transcribed audio/video content.
Given a text chunk from a media transcription (which may include speaker labels like [SPEAKER_00]),
extract all named entity mentions with precise character offsets.

## Output JSON Schema

Return a JSON object matching this schema:
{
  "mentions": [
    {
      "text": "string -- exact surface form as it appears in the input (NOT speaker labels)",
      "label": "string -- one of: PERSON, ORG, GPE, LOC, DATE, EVENT, MONEY, LAW, NORP, FACILITY, DOCUMENT, BOOK, ROLE, STRATEGIC_ASSET, FINANCIAL_INSTRUMENT, OTHER",
      "context": "string -- the sentence fragment containing the entity",
      "span_start": "integer -- character offset where the mention starts (0-based)",
      "span_end": "integer -- character offset where the mention ends (exclusive)"
    }
  ]
}

## Entity Type Definitions

- PERSON: speakers, interviewees, politicians mentioned by name (e.g. "Fidel Castro", "President Biden")
- ORG: companies, agencies, media outlets, PACs, think tanks (e.g. "CNN", "CIA", "Brookings Institution")
- GPE: countries, states, cities (e.g. "Russia", "Texas", "Kabul")
- EVENT: conferences, hearings, incidents, elections, wars (e.g. "the Iraq War", "G7 Summit")
- MONEY: financial figures, specific amounts (e.g. "$1.5 trillion", "200 million dollars")
- LAW: legislation, regulations, court cases, executive orders (e.g. "the Patriot Act", "Roe v. Wade")
- NORP: political parties, ethnic groups, national groups (e.g. "Republicans", "Iranians", "Sunni")
- STRATEGIC_ASSET: geopolitical chokepoints, pipelines, trade routes (e.g. "Strait of Hormuz", "Nord Stream")
- FINANCIAL_INSTRUMENT: stocks, bonds, funds, currencies (e.g. "S&P 500", "Treasury bonds", "Bitcoin")

## Examples

### Example 1 -- Interview transcript
Input: "[SPEAKER_00] So President Biden met with Xi Jinping in San Francisco last November to discuss Taiwan."
Output:
{"mentions": [
  {"text": "President Biden", "label": "PERSON", "context": "President Biden met with Xi Jinping in San Francisco", "span_start": 14, "span_end": 29},
  {"text": "Xi Jinping", "label": "PERSON", "context": "met with Xi Jinping in San Francisco", "span_start": 39, "span_end": 49},
  {"text": "San Francisco", "label": "GPE", "context": "Xi Jinping in San Francisco last November", "span_start": 53, "span_end": 66},
  {"text": "last November", "label": "DATE", "context": "in San Francisco last November", "span_start": 67, "span_end": 80},
  {"text": "Taiwan", "label": "GPE", "context": "to discuss Taiwan", "span_start": 92, "span_end": 98}
]}
Note: "[SPEAKER_00]" is a speaker label, NOT an entity.

### Example 2 -- Geopolitical discussion
Input: "The Houthis have been attacking ships near the Bab el-Mandeb strait, disrupting trade through the Suez Canal."
Output:
{"mentions": [
  {"text": "Houthis", "label": "NORP", "context": "The Houthis have been attacking ships", "span_start": 4, "span_end": 11},
  {"text": "Bab el-Mandeb strait", "label": "STRATEGIC_ASSET", "context": "near the Bab el-Mandeb strait", "span_start": 47, "span_end": 67},
  {"text": "Suez Canal", "label": "STRATEGIC_ASSET", "context": "trade through the Suez Canal", "span_start": 98, "span_end": 108}
]}

## Rules

1. No duplicate spans: do not extract the same (span_start, span_end) twice.
2. Do NOT extract speaker labels (SPEAKER_00, SPEAKER_01) as entities.
3. Do NOT extract pronouns (he, she, they, it, we, you, I) as entities.
4. White House is ORG, not GPE. Government buildings are FACILITY or ORG.
5. Political parties and national groups are NORP, not OTHER.
6. Use the most complete form of names (Fidel Castro, not just Fidel).
7. Be exhaustive but precise.""",
)


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
