"""Gold: Mention extraction via LLM — replaces flat NER entities.

Produces structured Mention objects with span offsets, domain-specific
entity guidance for leaked documents, and expanded type set.
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
    "mentions",
    fallback="""\
You are a named-entity extraction system specialized in leaked documents analysis.
Given a text chunk, extract all named entity mentions with precise information.

Entity types to extract:
- PERSON: diplomats, officials, intelligence officers, businesspeople, witnesses
- ORG: governments, corporations, shell companies, offshore entities, NGOs, law firms, banks
- GPE: countries, territories, tax havens, jurisdictions
- LOC: geographic features, regions, addresses
- DATE: specific dates, date ranges, time periods
- LAW: treaties, regulations, court cases, legal instruments
- EVENT: meetings, operations, investigations, transactions
- MONEY: financial amounts, transactions, transfers, investments
- NORP: nationalities, political groups, ethnic groups
- FACILITY: embassies, consulates, offices, buildings
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
    group_name="leaks",
    description="Extract entity mentions from leak document chunks via LLM (EDC gold layer)",
    compute_kind="llm",
    metadata={"layer": "gold"},
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def leak_mentions(
    context: AssetExecutionContext,
    llm: LLMResource,
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
        logger.info("Starting leak_mentions extraction for %d chunks", len(leak_chunks))
        chain = llm.with_structured_output(MentionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(content=MENTION_SYSTEM_PROMPT),
                HumanMessage(content=f"Extract all entity mentions from this text:\n\n{chunk.text}"),
            ],
            leak_chunks,
            operation="mention_extract",
        )

        all_mentions = build_mentions(
            leak_chunks,
            results,
            llm_model=llm.model,
            code_location="open_leaks",
        )

        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="leak_mentions", layer="gold").inc(
            len(all_mentions)
        )
        logger.info(
            "leak_mentions complete: %d mentions from %d chunks",
            len(all_mentions),
            len(leak_chunks),
        )
        context.log.info(f"Extracted {len(all_mentions)} mentions from {len(leak_chunks)} chunks")
        return Output(all_mentions, metadata={"mention_count": len(all_mentions)})
