"""Gold: Subject-Predicate-Object proposition extraction via LLM."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset
from dagster_io import LLMResource, TextChunk
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

SPO_SYSTEM_PROMPT = (
    "You are a knowledge-graph extraction system. "
    "Given a text chunk, extract Subject-Predicate-Object triples. "
    "Focus on factual, verifiable claims. Omit vague or opinion-based statements."
)


class Proposition(BaseModel):
    """A single S-P-O triple extracted from text."""

    subject: str = Field(description="The entity performing or being described")
    predicate: str = Field(description="The relationship or action")
    object: str = Field(description="The target entity or value")
    confidence: float = Field(description="Confidence score 0-1", ge=0, le=1)


class PropositionResult(BaseModel):
    """Structured output from proposition extraction."""

    propositions: list[Proposition] = Field(default_factory=list)


@asset(
    group_name="leaks",
    description="Extract S-P-O propositions from leak document chunks via LLM",
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
def leak_propositions(
    context: AssetExecutionContext,
    llm: LLMResource,
    leak_chunks: list[TextChunk],
) -> Output[list[dict[str, Any]]]:
    chain = llm.with_structured_output(PropositionResult)
    all_propositions: list[dict[str, Any]] = []

    for i, chunk in enumerate(leak_chunks):
        result: PropositionResult = chain.invoke([
            SystemMessage(content=SPO_SYSTEM_PROMPT),
            HumanMessage(
                content=f"Extract subject-predicate-object propositions from this text:\n\n{chunk.text}"
            ),
        ])
        for prop in result.propositions:
            all_propositions.append({
                **prop.model_dump(),
                "source_doc_id": chunk.document_id,
                "chunk_id": chunk.chunk_id,
            })

        if (i + 1) % 50 == 0:
            context.log.info(f"Processed {i + 1}/{len(leak_chunks)} chunks")

    context.log.info(
        f"Extracted {len(all_propositions)} propositions from {len(leak_chunks)} chunks"
    )
    return Output(all_propositions, metadata={"proposition_count": len(all_propositions)})
