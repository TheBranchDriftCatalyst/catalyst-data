"""Gold: Qualified assertion extraction via LLM — replaces flat propositions.

Produces structured Assertion objects with qualifiers (time, location, condition),
negation/hedging detection, and predicate normalization.
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
    "assertions",
    fallback="""\
You are a knowledge-graph extraction system specialized in U.S. Congressional data.
Given a text chunk, extract qualified Subject-Predicate-Object assertions.

Focus on factual, verifiable claims. Omit vague or opinion-based statements.

For each assertion, provide:
- subject: the entity performing or being described
- predicate: the relationship or action (use normalized verb forms: "sponsors", "member_of", "votes_for", "introduced", "co-sponsors", "chairs", "opposes")
- object: the target entity or value
- confidence: score 0-1 indicating how clearly the text supports this assertion
- negated: true if the assertion is negated ("did not", "failed to", "rejected")
- hedged: true if the assertion is uncertain ("may", "could", "reportedly", "is expected to", "allegedly")
- qualifiers: optional dict with keys:
  - time: when this occurred (date, session, period)
  - location: where (committee, chamber, jurisdiction)
  - condition: under what condition ("if passed", "pending approval")
  - manner: how ("unanimously", "by voice vote", "with amendments")
  - source_attribution: who says so ("according to", "as reported by")

Be precise with predicates. Prefer canonical forms over variations.""",
)

CONGRESS_PREDICATE_MAPPINGS = {
    "is a member of": "member_of",
    "is member of": "member_of",
    "belongs to": "member_of",
    "sponsored": "sponsors",
    "co-sponsored": "co_sponsors",
    "cosponsored": "co_sponsors",
    "introduced": "introduces",
    "voted for": "votes_for",
    "voted against": "votes_against",
    "chairs": "chairs",
    "chaired": "chairs",
    "opposes": "opposes",
    "opposed": "opposes",
    "supports": "supports",
    "supported": "supports",
}


@asset(
    group_name="congress",
    description="Extract qualified assertions from Congress document chunks via LLM (EDC gold layer)",
    compute_kind="llm",
    metadata={"layer": "gold"},
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def congress_assertions(
    context: AssetExecutionContext,
    llm: LLMResource,
    congress_chunks: list[TextChunk],
) -> Output[list[Assertion]]:
    with trace_operation(
        "congress_assertions",
        tracer,
        {
            "code_location": "congress_data",
            "layer": "gold",
            "chunk_count": len(congress_chunks),
        },
    ):
        logger.info(
            "Starting congress_assertions extraction for %d chunks",
            len(congress_chunks),
        )
        chain = llm.with_structured_output(AssertionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(content=ASSERTION_SYSTEM_PROMPT),
                HumanMessage(content=f"Extract qualified assertions from this text:\n\n{chunk.text}"),
            ],
            congress_chunks,
            operation="assertion_extract",
        )

        all_assertions = build_assertions(
            congress_chunks,
            results,
            llm_model=llm.model,
            code_location="congress_data",
            predicate_mappings=CONGRESS_PREDICATE_MAPPINGS,
        )

        negated_count = sum(1 for a in all_assertions if a.negated)
        hedged_count = sum(1 for a in all_assertions if a.hedged)
        ASSET_RECORDS_PROCESSED.labels(
            code_location="congress_data", asset_key="congress_assertions", layer="gold"
        ).inc(len(all_assertions))
        logger.info(
            "congress_assertions complete: %d assertions from %d chunks (negated=%d, hedged=%d)",
            len(all_assertions),
            len(congress_chunks),
            negated_count,
            hedged_count,
        )
        context.log.info(
            f"Extracted {len(all_assertions)} assertions from {len(congress_chunks)} chunks "
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
