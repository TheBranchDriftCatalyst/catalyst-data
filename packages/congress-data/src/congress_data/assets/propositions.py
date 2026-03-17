"""Gold: Subject-Predicate-Object proposition extraction via LLM."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset
from dagster_io import LLMResource, TextChunk
from dagster_io.prompts import load_prompt
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED, LLM_REQUEST_DURATION, track_duration
from dagster_io.observability import get_tracer, trace_operation

logger = get_logger(__name__)
tracer = get_tracer(__name__)

SPO_SYSTEM_PROMPT = load_prompt(
    "propositions/spo",
    fallback=(
        "You are a knowledge-graph extraction system. "
        "Given a text chunk, extract Subject-Predicate-Object triples. "
        "Focus on factual, verifiable claims. Omit vague or opinion-based statements."
    ),
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
    group_name="congress",
    description="Extract S-P-O propositions from Congress document chunks via LLM",
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
def congress_propositions(
    context: AssetExecutionContext,
    llm: LLMResource,
    congress_chunks: list[TextChunk],
) -> Output[list[dict[str, Any]]]:
    with trace_operation("congress_propositions", tracer, {"code_location": "congress_data", "layer": "gold", "chunk_count": len(congress_chunks)}):
        logger.info("Starting congress_propositions extraction for %d chunks", len(congress_chunks))
        chain = llm.with_structured_output(PropositionResult)
        all_propositions: list[dict[str, Any]] = []

        for i, chunk in enumerate(congress_chunks):
            logger.debug("Processing chunk %d/%d id=%s", i + 1, len(congress_chunks), chunk.chunk_id)
            with track_duration(LLM_REQUEST_DURATION, {"model": llm.model, "operation": "proposition_extract"}):
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
                context.log.info(f"Processed {i + 1}/{len(congress_chunks)} chunks")
                logger.info("Proposition progress: %d/%d chunks, %d propositions so far", i + 1, len(congress_chunks), len(all_propositions))

        ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_propositions", layer="gold").inc(len(all_propositions))
        logger.info("congress_propositions complete: %d propositions from %d chunks", len(all_propositions), len(congress_chunks))
        context.log.info(
            f"Extracted {len(all_propositions)} propositions from {len(congress_chunks)} chunks"
        )
        return Output(all_propositions, metadata={"proposition_count": len(all_propositions)})
