"""Gold: Qualified assertion extraction via LLM — replaces flat propositions.

Produces structured Assertion objects with qualifiers (time, location, condition),
negation/hedging detection, and predicate normalization for leaked documents.
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import (
    Assertion,
    LLMResource,
    Provenance,
    TextChunk,
)
from dagster_io.prompts import load_prompt
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSERTIONS_CREATED, ASSET_RECORDS_PROCESSED, LLM_REQUEST_DURATION, track_duration

logger = get_logger(__name__)

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


class AssertionQualifiers(BaseModel):
    """Qualifier fields for an assertion."""

    time: str = Field(description="When this occurred (date/period), or empty string if unknown")
    location: str = Field(description="Where (jurisdiction/country/embassy), or empty string if unknown")
    condition: str = Field(description="Under what condition, or empty string if none")
    manner: str = Field(description="How (secretly/through intermediaries/etc), or empty string if unknown")
    source_attribution: str = Field(description="Who says so, or empty string if not attributed")


class QualifiedAssertion(BaseModel):
    """A single qualified assertion extracted by the LLM."""

    subject: str = Field(description="Entity performing or being described")
    predicate: str = Field(description="Normalized relationship or action")
    object: str = Field(description="Target entity or value")
    confidence: float = Field(description="Score 0-1 indicating how clearly the text supports this")
    negated: bool = Field(description="True if this is a negative assertion")
    hedged: bool = Field(description="True if this is uncertain/hedged")
    qualifiers: AssertionQualifiers = Field(description="Qualifier fields for this assertion")


class AssertionExtractionResult(BaseModel):
    """Structured output from assertion extraction."""

    assertions: list[QualifiedAssertion] = Field(description="Extracted assertions")


def _normalize_predicate(predicate: str) -> str:
    """Basic predicate normalization for leaked documents domain."""
    norm = predicate.lower().strip()
    mappings = {
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
    return mappings.get(norm, norm)


@asset(
    group_name="leaks",
    description="Extract qualified assertions from leak document chunks via LLM (EDC gold layer)",
    compute_kind="llm",
    metadata={"layer": "gold"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "500m", "memory": "2Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi"},
                }
            }
        }
    },
)
def leak_assertions(
    context: AssetExecutionContext,
    llm: LLMResource,
    leak_chunks: list[TextChunk],
) -> Output[list[Assertion]]:
    logger.info("Starting leak_assertions extraction for %d chunks", len(leak_chunks))
    chain = llm.with_structured_output(AssertionExtractionResult)
    all_assertions: list[Assertion] = []

    for i, chunk in enumerate(leak_chunks):
        logger.debug("Processing chunk %d/%d id=%s", i + 1, len(leak_chunks), chunk.chunk_id)
        with track_duration(LLM_REQUEST_DURATION, {"model": llm.model, "operation": "assertion_extract"}):
            result: AssertionExtractionResult = chain.invoke([
                SystemMessage(content=ASSERTION_SYSTEM_PROMPT),
                HumanMessage(
                    content=f"Extract qualified assertions from this text:\n\n{chunk.text}"
                ),
            ])

        for ext in result.assertions:
            # Convert structured qualifiers to dict, dropping empty values
            quals = {k: v for k, v in ext.qualifiers.model_dump().items() if v}
            assertion = Assertion(
                subject_text=ext.subject,
                predicate=ext.predicate,
                predicate_canonical=_normalize_predicate(ext.predicate),
                object_text=ext.object,
                qualifiers=quals,
                confidence=ext.confidence,
                negated=ext.negated,
                hedged=ext.hedged,
                provenance=Provenance(
                    source_document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    extraction_model=llm.model,
                    confidence=ext.confidence,
                    code_location="open_leaks",
                ),
            )
            all_assertions.append(assertion)
            ASSERTIONS_CREATED.labels(code_location="open_leaks", predicate=ext.predicate[:50]).inc()
            if ext.confidence < 0.5:
                logger.warning("Low confidence assertion: subject=%s predicate=%s confidence=%.2f", ext.subject[:50], ext.predicate[:50], ext.confidence)

        if (i + 1) % 50 == 0:
            context.log.info(
                f"Processed {i + 1}/{len(leak_chunks)} chunks — {len(all_assertions)} assertions so far"
            )
            logger.info("Assertion progress: %d/%d chunks, %d assertions so far", i + 1, len(leak_chunks), len(all_assertions))

    negated_count = sum(1 for a in all_assertions if a.negated)
    hedged_count = sum(1 for a in all_assertions if a.hedged)
    ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="leak_assertions", layer="gold").inc(len(all_assertions))
    logger.info("leak_assertions complete: %d assertions from %d chunks (negated=%d, hedged=%d)", len(all_assertions), len(leak_chunks), negated_count, hedged_count)
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
