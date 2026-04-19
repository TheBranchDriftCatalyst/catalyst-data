"""Gold: Mention extraction via LLM — replaces flat NER entities.

Produces structured Mention objects with span offsets, domain-specific
entity guidance, and expanded type set.
"""

from dagster import AssetExecutionContext, Output, asset
from langchain_core.messages import HumanMessage, SystemMessage

from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    LLMResource,
    Mention,
    MentionExtractionResult,
    TextChunk,
    build_mentions,
)
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.prompts import load_prompt

logger = get_logger(__name__)
tracer = get_tracer(__name__)

MENTION_SYSTEM_PROMPT = load_prompt(
    "mentions/congress",
    fallback="""\
You are a named-entity extraction system specialized in U.S. Congressional data.
Given a text chunk, extract all named entity mentions with precise character offsets.

## Output JSON Schema

Return a JSON object matching this schema:
{
  "mentions": [
    {
      "text": "string -- exact surface form as it appears in the input",
      "label": "string -- one of: PERSON, ORG, GPE, LOC, DATE, LAW, EVENT, MONEY, NORP, FACILITY, DOCUMENT, BOOK, ROLE, STRATEGIC_ASSET, FINANCIAL_INSTRUMENT, OTHER",
      "context": "string -- the sentence fragment containing the entity",
      "span_start": "integer -- character offset where the mention starts (0-based)",
      "span_end": "integer -- character offset where the mention ends (exclusive)"
    }
  ]
}

## Entity Type Definitions

- PERSON: legislators, officials, witnesses, nominees (e.g. "Rep. Pelosi", "Sen. Schumer")
- ORG: committees, subcommittees, agencies, departments (e.g. "House Committee on Transportation", "EPA")
- GPE: countries, states, districts, cities (e.g. "California", "United States")
- DATE: specific dates, date ranges, congressional sessions (e.g. "March 15, 2025", "119th Congress 1st Session")
- LAW: bill numbers, public laws, acts, amendments (e.g. "H.R. 1234", "Clean Air Act", "Public Law 91-589")
- EVENT: hearings, votes, elections, investigations
- MONEY: specific dollar amounts (e.g. "$1.5 billion", "$200 million")
- NORP: political parties, caucuses, coalitions (e.g. "Republicans")

## Examples

### Example 1
Input: "Rep. Nancy Pelosi (D-CA) introduced H.R. 1234, the Clean Energy Innovation Act, on March 15, 2025."
Output:
{"mentions": [
  {"text": "Rep. Nancy Pelosi", "label": "PERSON", "context": "Rep. Nancy Pelosi (D-CA) introduced H.R. 1234", "span_start": 0, "span_end": 17},
  {"text": "H.R. 1234", "label": "LAW", "context": "introduced H.R. 1234, the Clean Energy Innovation Act", "span_start": 32, "span_end": 41},
  {"text": "Clean Energy Innovation Act", "label": "LAW", "context": "H.R. 1234, the Clean Energy Innovation Act", "span_start": 47, "span_end": 74},
  {"text": "March 15, 2025", "label": "DATE", "context": "on March 15, 2025", "span_start": 79, "span_end": 93}
]}
Note: "D-CA" is a party-state code, NOT an entity.

### Example 2
Input: "119th CONGRESS, 1st Session. S. 456. To amend the Social Security Act."
Output:
{"mentions": [
  {"text": "119th CONGRESS, 1st Session", "label": "DATE", "context": "119th CONGRESS, 1st Session", "span_start": 0, "span_end": 27},
  {"text": "S. 456", "label": "LAW", "context": "S. 456. To amend the Social Security Act", "span_start": 29, "span_end": 35},
  {"text": "Social Security Act", "label": "LAW", "context": "To amend the Social Security Act", "span_start": 50, "span_end": 69}
]}
Note: "119th CONGRESS" is a session identifier (DATE), NOT a PERSON or ORG.

## Rules

1. No duplicate spans: do not extract the same (span_start, span_end) twice.
2. Do NOT extract party-state codes ("R-TX", "D-CA") as entities.
3. "1st Session", "119th Congress" are DATE, not PERSON or ORG.
4. Committees are ORG, not GPE.
5. Extract MONEY only for specific dollar amounts.
6. Be exhaustive but precise.""",
)


@asset(
    group_name="congress",
    description="Extract entity mentions from Congress document chunks via LLM (EDC gold layer)",
    compute_kind="llm",
    metadata={"layer": "gold"},
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def congress_mentions(
    context: AssetExecutionContext,
    llm: LLMResource,
    congress_chunks: list[TextChunk],
) -> Output[list[Mention]]:
    with trace_operation(
        "congress_mentions",
        tracer,
        {
            "code_location": "congress_data",
            "layer": "gold",
            "chunk_count": len(congress_chunks),
        },
    ):
        logger.info("Starting congress_mentions extraction for %d chunks", len(congress_chunks))
        chain = llm.with_structured_output(MentionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(content=MENTION_SYSTEM_PROMPT),
                HumanMessage(content=f"Extract all entity mentions from this text:\n\n{chunk.text}"),
            ],
            congress_chunks,
            operation="mention_extract",
        )

        all_mentions = build_mentions(
            congress_chunks,
            results,
            llm_model=llm.model,
            code_location="congress_data",
        )

        ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_mentions", layer="gold").inc(
            len(all_mentions)
        )
        logger.info(
            "congress_mentions complete: %d mentions from %d chunks",
            len(all_mentions),
            len(congress_chunks),
        )
        context.log.info(f"Extracted {len(all_mentions)} mentions from {len(congress_chunks)} chunks")
        return Output(all_mentions, metadata={"mention_count": len(all_mentions)})
