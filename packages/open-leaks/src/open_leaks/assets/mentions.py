"""Gold: Mention extraction via LLM — replaces flat NER entities.

Produces structured Mention objects with span offsets, domain-specific
entity guidance for leaked documents, and expanded type set.
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import (
    LLMResource,
    Mention,
    MentionType,
    Provenance,
    TextChunk,
)
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

MENTION_SYSTEM_PROMPT = """\
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

Be exhaustive but avoid duplicates within the same span."""


class MentionExtraction(BaseModel):
    """A single mention extracted by the LLM."""

    text: str = Field(description="Entity mention as it appears in text")
    label: str = Field(description="Entity type: PERSON, ORG, GPE, LOC, DATE, LAW, EVENT, MONEY, NORP, FACILITY, OTHER")
    context: str = Field(description="Sentence fragment containing the entity")
    span_start: int = Field(description="Character offset start (0-based), or -1 if unknown")
    span_end: int = Field(description="Character offset end (exclusive), or -1 if unknown")


class MentionExtractionResult(BaseModel):
    """Structured output from mention extraction."""

    mentions: list[MentionExtraction] = Field(description="Extracted entity mentions")


def _parse_mention_type(label: str) -> MentionType:
    """Parse LLM label string to MentionType enum, with fallback."""
    try:
        return MentionType(label.upper().strip())
    except ValueError:
        return MentionType.OTHER


@asset(
    group_name="leaks",
    description="Extract entity mentions from leak document chunks via LLM (EDC gold layer)",
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
def leak_mentions(
    context: AssetExecutionContext,
    llm: LLMResource,
    leak_chunks: list[TextChunk],
) -> Output[list[Mention]]:
    chain = llm.with_structured_output(MentionExtractionResult)
    all_mentions: list[Mention] = []

    for i, chunk in enumerate(leak_chunks):
        result: MentionExtractionResult = chain.invoke([
            SystemMessage(content=MENTION_SYSTEM_PROMPT),
            HumanMessage(content=f"Extract all entity mentions from this text:\n\n{chunk.text}"),
        ])

        for ext in result.mentions:
            mention = Mention(
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                text=ext.text,
                mention_type=_parse_mention_type(ext.label),
                span_start=ext.span_start if ext.span_start >= 0 else None,
                span_end=ext.span_end if ext.span_end >= 0 else None,
                context=ext.context,
                provenance=Provenance(
                    source_document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    span_start=ext.span_start if ext.span_start >= 0 else None,
                    span_end=ext.span_end if ext.span_end >= 0 else None,
                    extraction_model=llm.model,
                    code_location="open_leaks",
                ),
            )
            all_mentions.append(mention)

        if (i + 1) % 50 == 0:
            context.log.info(f"Processed {i + 1}/{len(leak_chunks)} chunks — {len(all_mentions)} mentions so far")

    context.log.info(f"Extracted {len(all_mentions)} mentions from {len(leak_chunks)} chunks")
    return Output(all_mentions, metadata={"mention_count": len(all_mentions)})
