"""Gold: Mention extraction from media transcription chunks via LLM.

Partitioned by document_id — each run extracts mentions from one document's chunks.
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
from langchain_core.messages import HumanMessage, SystemMessage

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

MENTION_SYSTEM_PROMPT = """\
You are a named-entity extraction system specialized in transcribed audio/video content.
Given a text chunk from a media transcription (which may include speaker labels like [SPEAKER_00]),
extract all named entity mentions with precise information.

Entity types to extract:
- PERSON: speakers, interviewees, people mentioned by name
- ORG: companies, agencies, institutions, media outlets
- GPE: countries, states, cities
- LOC: geographic regions, landmarks
- DATE: specific dates, time periods, years
- EVENT: conferences, hearings, incidents, elections
- MONEY: financial figures, amounts, valuations
- LAW: legislation, regulations, court cases, executive orders
- DOCUMENT: reports, studies, publications referenced
- ROLE: job titles, positions (e.g. "CEO", "Senator", "Director")
- OTHER: any other notable entity

For each entity, provide:
- text: the exact mention as it appears (NOT the speaker label)
- label: entity type from the list above
- context: the sentence fragment containing the entity
- span_start: character offset where the mention starts in the input text (0-based)
- span_end: character offset where the mention ends (exclusive)

Important:
- Do NOT extract speaker labels (SPEAKER_00, SPEAKER_01) as entities
- DO extract people mentioned BY NAME within speaker dialogue
- Preserve speaker context when relevant (who said what about whom)
- Be exhaustive but avoid duplicates within the same span.

IMPORTANT RULES:
- Do NOT extract pronoun-only mentions (he, she, they, it, we, you, I, someone, people)
- Resolve pronouns to the actual entity name when context makes it clear
- White House is ORG (institution), not GPE. Government buildings are FACILITY or ORG.
- Political parties and national groups (Republicans, Democrats, Cubans, Iranians) are NORP, not OTHER
- Use the most complete form of names (Fidel Castro, not just Fidel)
- If the same entity appears with different surface forms, prefer the most specific one"""


@asset(
    group_name="media_ingest",
    description="Extract entity mentions from one document's transcription chunks via LLM",
    compute_kind="llm",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def media_mentions(
    context: AssetExecutionContext,
    llm: LLMResource,
    media_chunks: list[TextChunk],
) -> Output[list[Mention]]:
    partition_key = context.partition_key
    with trace_operation("media_mentions", tracer, {"code_location": "media_ingest", "layer": "gold", "partition_key": partition_key, "chunk_count": len(media_chunks)}):
        logger.info("Starting media_mentions extraction for partition=%s (%d chunks)", partition_key, len(media_chunks))

        if not media_chunks:
            context.log.info(f"No chunks for partition={partition_key} — returning empty mentions")
            return Output([], metadata={"mention_count": 0, "document_id": partition_key})

        chain = llm.with_structured_output(MentionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(content=MENTION_SYSTEM_PROMPT),
                HumanMessage(content=f"Extract all entity mentions from this text:\n\n{chunk.text}"),
            ],
            media_chunks,
            operation="mention_extract",
        )

        all_mentions = build_mentions(
            media_chunks, results, llm_model=llm.model, code_location="media_ingest",
        )

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_mentions", layer="gold").inc(len(all_mentions))
        logger.info("media_mentions complete for partition=%s: %d mentions from %d chunks", partition_key, len(all_mentions), len(media_chunks))
        context.log.info(f"Extracted {len(all_mentions)} mentions from {len(media_chunks)} chunks")
        return Output(
            all_mentions,
            metadata={
                "document_id": partition_key,
                "mention_count": len(all_mentions),
                "chunk_count": len(media_chunks),
            },
        )
