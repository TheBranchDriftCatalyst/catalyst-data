"""Gold: Mention extraction via LLM — replaces flat NER entities.

Produces structured Mention objects with span offsets, domain-specific
entity guidance, and expanded type set.
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    LLMResource,
    Mention,
    MentionExtractionResult,
    TextChunk,
    build_mentions,
)
from dagster_io.prompts import load_prompt
from langchain_core.messages import HumanMessage, SystemMessage

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation

logger = get_logger(__name__)
tracer = get_tracer(__name__)

MENTION_SYSTEM_PROMPT = load_prompt(
    "mentions/congress",
    fallback="""\
You are a named-entity extraction system specialized in U.S. Congressional data.
Given a text chunk, extract all named entity mentions with precise information.

Entity types to extract:
- PERSON: legislators, officials, witnesses, nominees
- ORG: committees, subcommittees, agencies, departments, lobbying groups
- GPE: countries, states, districts, cities
- LOC: geographic features, regions
- DATE: specific dates, date ranges, congressional sessions
- LAW: bill numbers (H.R. XXX, S. XXX), public laws, acts, amendments
- EVENT: hearings, votes, elections, investigations
- MONEY: appropriations, budget figures, funding amounts
- NORP: political parties, caucuses, coalitions
- FACILITY: government buildings, military bases
- OTHER: any other notable entity

For each entity, provide:
- text: the exact mention as it appears
- label: entity type from the list above
- context: the sentence fragment containing the entity
- span_start: character offset where the mention starts in the input text (0-based)
- span_end: character offset where the mention ends (exclusive)

Be exhaustive but avoid duplicates within the same span.""",
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
    with trace_operation("congress_mentions", tracer, {"code_location": "congress_data", "layer": "gold", "chunk_count": len(congress_chunks)}):
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
            congress_chunks, results, llm_model=llm.model, code_location="congress_data",
        )

        ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_mentions", layer="gold").inc(len(all_mentions))
        logger.info("congress_mentions complete: %d mentions from %d chunks", len(all_mentions), len(congress_chunks))
        context.log.info(f"Extracted {len(all_mentions)} mentions from {len(congress_chunks)} chunks")
        return Output(all_mentions, metadata={"mention_count": len(all_mentions)})
