"""Gold: Qualified assertion extraction via LLM — replaces flat propositions.

Produces structured Assertion objects with qualifiers (time, location, condition),
negation/hedging detection, and predicate normalization for leaked documents.
"""

from dagster import AssetExecutionContext, Output, asset
from langchain_core.messages import HumanMessage, SystemMessage

from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    Assertion,
    AssertionExtractionResult,
    LLMResource,
    TextChunk,
    build_assertions,
)
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.prompts import load_prompt

logger = get_logger(__name__)
tracer = get_tracer(__name__)

ASSERTION_SYSTEM_PROMPT = load_prompt(
    "assertions/leaks",
    fallback="""\
You are a knowledge-graph extraction system specialized in leaked documents analysis.
Given a text chunk, extract qualified Subject-Predicate-Object assertions.

Focus on factual, verifiable claims. Omit vague or opinion-based statements.

For each assertion, provide:
- subject: the entity performing or being described
- predicate: the relationship or action (use normalized verb forms: "owns", "directs", "transfers_to", "registered_in", "associated_with", "reports_to", "finances")
- object: the target entity or value
- confidence: score 0-1 indicating how clearly the text supports this assertion
- negated: true if the assertion is negated ("did not", "denied", "no evidence of")
- hedged: true if the assertion is uncertain ("may", "could", "reportedly", "is believed to", "allegedly")
- qualifiers: optional dict with keys:
  - time: when this occurred (date, period)
  - location: where (jurisdiction, country, embassy)
  - condition: under what condition
  - manner: how ("secretly", "through intermediaries")
  - source_attribution: who says so ("according to cable", "per ICIJ records")

Be precise with predicates. Prefer canonical forms over variations.""",
)

LEAKS_PREDICATE_MAPPINGS = {
    "is owned by": "owned_by",
    "owns": "owns",
    "directed": "directs",
    "directs": "directs",
    "transferred to": "transfers_to",
    "transferred funds to": "transfers_to",
    "is registered in": "registered_in",
    "registered in": "registered_in",
    "incorporated in": "registered_in",
    "associated with": "associated_with",
    "is associated with": "associated_with",
    "linked to": "associated_with",
    "reports to": "reports_to",
    "financed": "finances",
    "finances": "finances",
    "funded": "finances",
}


@asset(
    group_name="leaks",
    description="Extract qualified assertions from leak document chunks via LLM (EDC gold layer)",
    compute_kind="llm",
    metadata={"layer": "gold"},
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def leak_assertions(
    context: AssetExecutionContext,
    llm: LLMResource,
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
        logger.info("Starting leak_assertions extraction for %d chunks", len(leak_chunks))
        chain = llm.with_structured_output(AssertionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(content=ASSERTION_SYSTEM_PROMPT),
                HumanMessage(content=f"Extract qualified assertions from this text:\n\n{chunk.text}"),
            ],
            leak_chunks,
            operation="assertion_extract",
        )

        all_assertions = build_assertions(
            leak_chunks,
            results,
            llm_model=llm.model,
            code_location="open_leaks",
            predicate_mappings=LEAKS_PREDICATE_MAPPINGS,
        )

        negated_count = sum(1 for a in all_assertions if a.negated)
        hedged_count = sum(1 for a in all_assertions if a.hedged)
        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="leak_assertions", layer="gold").inc(
            len(all_assertions)
        )
        logger.info(
            "leak_assertions complete: %d assertions from %d chunks (negated=%d, hedged=%d)",
            len(all_assertions),
            len(leak_chunks),
            negated_count,
            hedged_count,
        )
        context.log.info(
            f"Extracted {len(all_assertions)} assertions from {len(leak_chunks)} chunks "
            f"({negated_count} negated, {hedged_count} hedged)"
        )
        return Output(
            all_assertions,
            metadata={
                "assertion_count": len(all_assertions),
                "negated_count": negated_count,
                "hedged_count": hedged_count,
            },
        )
