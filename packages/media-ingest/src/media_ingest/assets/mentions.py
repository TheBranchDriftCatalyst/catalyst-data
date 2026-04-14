"""Gold: Mention extraction from media transcription chunks via LLM.

Partitioned by document_id — each run extracts mentions from one document's chunks.
"""

from dagster import AssetExecutionContext, Output, asset

from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    Mention,
    TextChunk,
)
from dagster_io.extraction import extract_validated
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
- ORG: companies, agencies, institutions, media outlets, PACs, think tanks
- GPE: countries, states, cities
- LOC: geographic regions, landmarks, bodies of water
- DATE: specific dates, time periods, years
- EVENT: conferences, hearings, incidents, elections, wars, summits
- MONEY: financial figures, amounts, valuations
- LAW: legislation, regulations, court cases, executive orders
- NORP: political parties, ethnic groups, national groups (Republicans, Iranians, Sunni)
- FACILITY: buildings, military bases, embassies, airports
- DOCUMENT: reports, studies, publications referenced ("the Mueller Report", "the 9/11 Commission Report")
- BOOK: books, authored works ("The Art of the Deal", "Mein Kampf", "Capital")
- ROLE: job titles, positions ("Secretary of State", "CEO", "Chairman of the Joint Chiefs")
- STRATEGIC_ASSET: geopolitical chokepoints, pipelines, trade routes, military installations ("Strait of Hormuz", "Bab el-Mandeb", "Nord Stream", "Suez Canal", "Diego Garcia", "Pine Gap")
- FINANCIAL_INSTRUMENT: stocks, bonds, funds, derivatives, currencies ("S&P 500", "Treasury bonds", "Bitcoin", "petrodollar")
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
    media_chunks: list[TextChunk],
) -> Output[list[Mention]]:
    partition_key = context.partition_key
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

        all_mentions, _ = extract_validated(
            media_chunks,
            code_location="media_ingest",
            max_concurrency=5,
        )

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_mentions", layer="gold").inc(
            len(all_mentions)
        )
        context.log.info(f"Extracted {len(all_mentions)} validated mentions from {len(media_chunks)} chunks")
        return Output(
            all_mentions,
            metadata={
                "document_id": partition_key,
                "mention_count": len(all_mentions),
                "chunk_count": len(media_chunks),
            },
        )
