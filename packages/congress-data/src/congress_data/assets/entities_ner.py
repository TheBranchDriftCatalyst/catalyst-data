"""Silver: Named Entity Recognition (NER) extraction via LLM."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset
from dagster_io import LLMResource, TextChunk
from dagster_io.prompts import load_prompt
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED, ENTITIES_EXTRACTED, LLM_REQUEST_DURATION, track_duration

logger = get_logger(__name__)

NER_SYSTEM_PROMPT = load_prompt(
    "ner/basic",
    fallback=(
        "You are a named-entity extraction system. "
        "Given a text chunk, extract all named entities. "
        "Be exhaustive but avoid duplicates."
    ),
)


class Entity(BaseModel):
    """A single named entity extracted from text."""

    text: str = Field(description="The entity mention as it appears in the text")
    label: str = Field(description="Entity type: PERSON, ORG, GPE, DATE, LAW, or EVENT")
    context: str = Field(description="The sentence fragment where the entity appears")


class NERResult(BaseModel):
    """Structured output from NER extraction."""

    entities: list[Entity] = Field(default_factory=list)


@asset(
    group_name="congress",
    description="Extract named entities from Congress document chunks via LLM",
    compute_kind="llm",
    metadata={"layer": "silver"},
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
def congress_entities(
    context: AssetExecutionContext,
    llm: LLMResource,
    congress_chunks: list[TextChunk],
) -> Output[list[dict[str, Any]]]:
    logger.info("Starting congress_entities NER extraction for %d chunks", len(congress_chunks))
    chain = llm.with_structured_output(NERResult)
    all_entities: list[dict[str, Any]] = []

    for i, chunk in enumerate(congress_chunks):
        logger.debug("Processing chunk %d/%d id=%s", i + 1, len(congress_chunks), chunk.chunk_id)
        with track_duration(LLM_REQUEST_DURATION, {"model": llm.model, "operation": "ner_extract"}):
            result: NERResult = chain.invoke([
                SystemMessage(content=NER_SYSTEM_PROMPT),
                HumanMessage(content=f"Extract named entities from this text:\n\n{chunk.text}"),
            ])
        for ent in result.entities:
            all_entities.append({
                **ent.model_dump(),
                "source_doc_id": chunk.document_id,
                "chunk_id": chunk.chunk_id,
            })
            ENTITIES_EXTRACTED.labels(code_location="congress_data", entity_type=ent.label, method="llm").inc()

        if (i + 1) % 50 == 0:
            context.log.info(f"Processed {i + 1}/{len(congress_chunks)} chunks")
            logger.info("NER progress: %d/%d chunks, %d entities so far", i + 1, len(congress_chunks), len(all_entities))

    ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_entities", layer="silver").inc(len(all_entities))
    logger.info("congress_entities NER complete: %d entities from %d chunks", len(all_entities), len(congress_chunks))
    context.log.info(f"Extracted {len(all_entities)} entities from {len(congress_chunks)} chunks")
    return Output(all_entities, metadata={"entity_count": len(all_entities)})
