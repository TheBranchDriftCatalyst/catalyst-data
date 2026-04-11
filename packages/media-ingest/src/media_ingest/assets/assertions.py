"""Gold: Qualified assertion extraction from media transcription chunks via LLM.

Partitioned by document_id — each run extracts assertions from one document's chunks.
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
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

ASSERTION_SYSTEM_PROMPT = """\
You are a knowledge-graph extraction system specialized in transcribed audio/video content.
Given a text chunk from a media transcription (which may include speaker labels),
extract qualified Subject-Predicate-Object assertions.

Focus on factual, verifiable claims made by speakers. Capture both:
- Claims about the world (facts, allegations, statements)
- Speech acts (who said what, who questioned whom)

For each assertion, provide:
- subject: the entity performing or being described
- predicate: the relationship or action (use normalized verb forms)
- object: the target entity or value
- confidence: score 0-1 indicating how clearly the text supports this assertion
- negated: true if the assertion is negated ("did not", "denies", "rejected")
- hedged: true if uncertain ("may", "could", "reportedly", "allegedly", "is expected to")
- qualifiers: optional dict with keys:
  - time: when this occurred or was discussed
  - location: where (venue, jurisdiction, context)
  - condition: under what condition
  - manner: how ("emphatically", "under oath", "off the record")
  - source_attribution: who says so ("according to SPEAKER_00", "as reported by")

Prefer these canonical predicates for speech acts:
  states, claims, denies, confirms, acknowledges, questions,
  responds_to, references, discusses, criticizes, supports, opposes

Be precise with predicates. Preserve speaker attribution in source_attribution qualifier.

IMPORTANT RULES:
- Do NOT use pronouns as subjects or objects. Resolve to the actual entity name.
- Use SPEAKER_XX labels exactly as they appear (e.g., SPEAKER_00, SPEAKER_01), not 'Speaker' or 'the speaker'
- Skip meta-commentary about the conversation itself (e.g., 'this is just guessing', 'I want to get someone on the show')
- Only extract factual claims, allegations, and substantive speech acts — not conversational filler
- Each assertion should be independently meaningful without needing surrounding context"""

# Speech-act predicates for media content, with bridges to congress/leaks vocabularies
MEDIA_PREDICATE_MAPPINGS = {
    # Speech acts
    "said": "states",
    "says": "states",
    "stated": "states",
    "claimed": "claims",
    "alleges": "claims",
    "alleged": "claims",
    "denied": "denies",
    "confirmed": "confirms",
    "acknowledged": "acknowledges",
    "admitted": "acknowledges",
    "questioned": "questions",
    "asked": "questions",
    "responded": "responds_to",
    "replied": "responds_to",
    "referenced": "references",
    "mentioned": "references",
    "cited": "references",
    "discussed": "discusses",
    "talked about": "discusses",
    "criticized": "criticizes",
    "condemned": "criticizes",
    "attacked": "criticizes",
    # Bridge to congress vocabulary
    "endorsed": "supports",
    "supported": "supports",
    "backed": "supports",
    "opposed": "opposes",
    "rejected": "opposes",
    # Bridge to leaks vocabulary
    "owns": "owns",
    "transferred": "transfers",
    "operates": "operates",
    # General
    "is a member of": "member_of",
    "works for": "works_for",
    "works at": "works_for",
    "leads": "leads",
    "founded": "founded",
    "created": "created",
    # Filter vague predicates (mapped to empty string = skip)
    "is": "",
    "are": "",
    "was": "",
    "were": "",
    "have": "",
    "has": "",
    "had": "",
    "got": "",
    "came": "",
    "went": "",
    # Additional normalizations
    "left": "departed",
    "spiked": "increased",
    "provided": "provides",
    "rose": "increased",
    "fell": "decreased",
    "dropped": "decreased",
}


@asset(
    group_name="media_ingest",
    description="Extract qualified assertions from one document's transcription chunks via LLM",
    compute_kind="llm",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def media_assertions(
    context: AssetExecutionContext,
    llm: LLMResource,
    media_chunks: list[TextChunk],
) -> Output[list[Assertion]]:
    partition_key = context.partition_key
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

        chain = llm.with_structured_output(AssertionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(content=ASSERTION_SYSTEM_PROMPT),
                HumanMessage(content=f"Extract qualified assertions from this text:\n\n{chunk.text}"),
            ],
            media_chunks,
            operation="assertion_extract",
        )

        all_assertions = build_assertions(
            media_chunks,
            results,
            llm_model=llm.model,
            code_location="media_ingest",
            predicate_mappings=MEDIA_PREDICATE_MAPPINGS,
        )

        negated_count = sum(1 for a in all_assertions if a.negated)
        hedged_count = sum(1 for a in all_assertions if a.hedged)
        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_assertions", layer="gold").inc(
            len(all_assertions)
        )
        logger.info(
            "media_assertions complete for partition=%s: %d assertions from %d chunks (negated=%d, hedged=%d)",
            partition_key,
            len(all_assertions),
            len(media_chunks),
            negated_count,
            hedged_count,
        )
        context.log.info(
            f"Extracted {len(all_assertions)} assertions from {len(media_chunks)} chunks "
            f"({negated_count} negated, {hedged_count} hedged)"
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
