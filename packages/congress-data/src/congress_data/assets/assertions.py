"""Gold: Qualified assertion extraction via LLM — replaces flat propositions.

Produces structured Assertion objects with qualifiers (time, location, condition),
negation/hedging detection, and predicate normalization.
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import (
    Assertion,
    LLMResource,
    Provenance,
    TextChunk,
)
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

ASSERTION_SYSTEM_PROMPT = """\
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

Be precise with predicates. Prefer canonical forms over variations."""


class AssertionQualifiers(BaseModel):
    """Qualifier fields for an assertion."""

    time: str = Field(description="When this occurred (date/session/period), or empty string if unknown")
    location: str = Field(description="Where (committee/chamber/jurisdiction), or empty string if unknown")
    condition: str = Field(description="Under what condition, or empty string if none")
    manner: str = Field(description="How (unanimously/by voice vote/etc), or empty string if unknown")
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
    """Basic predicate normalization."""
    norm = predicate.lower().strip()
    # Common normalizations for congressional data
    mappings = {
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
    return mappings.get(norm, norm)


@asset(
    group_name="congress",
    description="Extract qualified assertions from Congress document chunks via LLM (EDC gold layer)",
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
def congress_assertions(
    context: AssetExecutionContext,
    llm: LLMResource,
    congress_chunks: list[TextChunk],
) -> Output[list[Assertion]]:
    chain = llm.with_structured_output(AssertionExtractionResult)
    all_assertions: list[Assertion] = []

    for i, chunk in enumerate(congress_chunks):
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
                    code_location="congress_data",
                ),
            )
            all_assertions.append(assertion)

        if (i + 1) % 50 == 0:
            context.log.info(
                f"Processed {i + 1}/{len(congress_chunks)} chunks — {len(all_assertions)} assertions so far"
            )

    negated_count = sum(1 for a in all_assertions if a.negated)
    hedged_count = sum(1 for a in all_assertions if a.hedged)
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
