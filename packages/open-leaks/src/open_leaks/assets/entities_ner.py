"""Silver: Named Entity Recognition via LLM."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset
from dagster_io import LLMResource, TextChunk
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

NER_SYSTEM_PROMPT = (
    "You are a named-entity extraction system. "
    "Given a text chunk, extract all named entities. "
    "Be exhaustive but avoid duplicates."
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
    group_name="leaks",
    description="Extract named entities from leak document chunks via LLM",
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
def leak_entities(
    context: AssetExecutionContext,
    llm: LLMResource,
    leak_chunks: list[TextChunk],
) -> Output[list[dict[str, Any]]]:
    chain = llm.with_structured_output(NERResult)
    all_entities: list[dict[str, Any]] = []

    for i, chunk in enumerate(leak_chunks):
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

        if (i + 1) % 50 == 0:
            context.log.info(f"Processed {i + 1}/{len(leak_chunks)} chunks")

    context.log.info(f"Extracted {len(all_entities)} entities from {len(leak_chunks)} chunks")
    return Output(all_entities, metadata={"entity_count": len(all_entities)})
