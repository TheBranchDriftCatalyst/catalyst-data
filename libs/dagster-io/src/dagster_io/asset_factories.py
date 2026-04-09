"""Shared asset builders and factories for LLM extraction pipelines.

Provides:
- ``LLM_ASSET_K8S_CONFIG``: Standard k8s resource config for LLM assets.
- ``build_mentions()``: Convert LLM extraction results into Mention objects.
- ``build_assertions()``: Convert LLM extraction results into Assertion objects.
- ``make_ner_asset()``: Factory for NER extraction assets.
- ``make_proposition_asset()``: Factory for S-P-O proposition extraction assets.
"""

from __future__ import annotations

from typing import Any

from dagster import AssetIn, Output, asset
from langchain_core.messages import HumanMessage, SystemMessage

from dagster_io.extraction_schemas import (
    AssertionExtractionResult,
    MentionExtraction,
    NERResult,
    PropositionResult,
    normalize_predicate,
    parse_mention_type,
)
from dagster_io.llm import LLMResource
from dagster_io.logging import get_logger
from dagster_io.metrics import (
    ASSERTIONS_CREATED,
    ASSET_RECORDS_PROCESSED,
    ENTITIES_EXTRACTED,
)
from dagster_io.models import Assertion, Mention, Provenance
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.prompts import load_prompt

logger = get_logger(__name__)

# ── Shared k8s config ────────────────────────────────────────────────

LLM_ASSET_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "resources": {
                "requests": {"cpu": "500m", "memory": "2Gi"},
                "limits": {"cpu": "2", "memory": "4Gi"},
            }
        }
    }
}


# ── Mention builder ─────────────────────────────────────────────────


def build_mentions(
    chunks: list,
    results: list,
    *,
    llm_model: str,
    code_location: str,
) -> list[Mention]:
    """Convert LLM MentionExtractionResult objects into Mention domain models."""
    mentions: list[Mention] = []
    for chunk, result in zip(chunks, results):
        ext: MentionExtraction
        for ext in result.mentions:
            ENTITIES_EXTRACTED.labels(
                code_location=code_location, entity_type=ext.label, method="llm"
            ).inc()
            mentions.append(
                Mention(
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    text=ext.text,
                    mention_type=parse_mention_type(ext.label),
                    span_start=ext.span_start if ext.span_start >= 0 else None,
                    span_end=ext.span_end if ext.span_end >= 0 else None,
                    context=ext.context,
                    provenance=Provenance(
                        source_document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                        span_start=ext.span_start if ext.span_start >= 0 else None,
                        span_end=ext.span_end if ext.span_end >= 0 else None,
                        extraction_model=llm_model,
                        code_location=code_location,
                    ),
                )
            )
    return mentions


# ── Assertion builder ────────────────────────────────────────────────


def build_assertions(
    chunks: list,
    results: list,
    *,
    llm_model: str,
    code_location: str,
    predicate_mappings: dict[str, str],
) -> list[Assertion]:
    """Convert LLM AssertionExtractionResult objects into Assertion domain models."""
    # Post-filter: skip low-quality assertions
    PRONOUN_SUBJECTS = {"he", "she", "they", "it", "we", "you", "i", "someone", "people", "them", "him", "her"}

    assertions: list[Assertion] = []
    for chunk, result in zip(chunks, results):
        for ext in result.assertions:
            # Skip pronoun subjects
            subj_lower = ext.subject.lower().strip()
            if subj_lower in PRONOUN_SUBJECTS:
                continue
            # Skip very low confidence
            if ext.confidence < 0.3:
                continue
            # Skip overly long objects (likely summaries)
            if len(ext.object) > 200:
                continue
            # Skip filtered predicates (mapped to empty string)
            canonical = normalize_predicate(ext.predicate, predicate_mappings)
            if canonical == "":
                continue

            quals = {k: v for k, v in ext.qualifiers.model_dump().items() if v}
            assertions.append(
                Assertion(
                    subject_text=ext.subject,
                    predicate=ext.predicate,
                    predicate_canonical=canonical,
                    object_text=ext.object,
                    qualifiers=quals,
                    confidence=ext.confidence,
                    negated=ext.negated,
                    hedged=ext.hedged,
                    provenance=Provenance(
                        source_document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                        extraction_model=llm_model,
                        confidence=ext.confidence,
                        code_location=code_location,
                    ),
                )
            )
            ASSERTIONS_CREATED.labels(code_location=code_location).inc()
            if ext.confidence < 0.5:
                logger.warning(
                    "Low confidence assertion: subject=%s predicate=%s confidence=%.2f",
                    ext.subject[:50],
                    ext.predicate[:50],
                    ext.confidence,
                )
    return assertions


# ── NER asset factory ────────────────────────────────────────────────

_NER_SYSTEM_PROMPT = load_prompt(
    "ner/basic",
    fallback=(
        "You are a named-entity extraction system. "
        "Given a text chunk, extract all named entities. "
        "Be exhaustive but avoid duplicates."
    ),
)


def make_ner_asset(
    *,
    group_name: str,
    code_location: str,
    input_key: str,
    asset_name: str,
    layer: str = "silver",
):
    """Create a NER extraction asset for a given code location.

    Args:
        group_name: Dagster group (e.g. "congress", "leaks").
        code_location: Metrics label (e.g. "congress_data", "open_leaks").
        input_key: Name of the upstream chunks parameter (e.g. "congress_chunks").
        asset_name: Name for the generated asset (e.g. "congress_entities").
        layer: EDC layer label for metadata.
    """
    tracer = get_tracer(f"{code_location}.{asset_name}")

    @asset(
        name=asset_name,
        group_name=group_name,
        description=f"Extract named entities from {group_name} document chunks via LLM",
        compute_kind="llm",
        metadata={"layer": layer},
        op_tags=LLM_ASSET_K8S_CONFIG,
        ins={input_key: AssetIn()},
    )
    def _ner_asset(
        context,
        llm: LLMResource,
        **kwargs: Any,
    ) -> Output[list[dict[str, Any]]]:
        chunks = kwargs[input_key]
        with trace_operation(
            asset_name, tracer, {"code_location": code_location, "layer": layer, "chunk_count": len(chunks)}
        ):
            logger.info("Starting %s NER extraction for %d chunks", asset_name, len(chunks))
            chain = llm.with_structured_output(NERResult)
            results = llm.invoke_batch(
                chain,
                lambda chunk: [
                    SystemMessage(content=_NER_SYSTEM_PROMPT),
                    HumanMessage(content=f"Extract named entities from this text:\n\n{chunk.text}"),
                ],
                chunks,
                operation="ner_extract",
            )

            all_entities: list[dict[str, Any]] = []
            for chunk, result in zip(chunks, results):
                for ent in result.entities:
                    all_entities.append({
                        **ent.model_dump(),
                        "source_doc_id": chunk.document_id,
                        "chunk_id": chunk.chunk_id,
                    })
                    ENTITIES_EXTRACTED.labels(
                        code_location=code_location, entity_type=ent.label, method="llm"
                    ).inc()

            ASSET_RECORDS_PROCESSED.labels(
                code_location=code_location, asset_key=asset_name, layer=layer
            ).inc(len(all_entities))
            logger.info("%s NER complete: %d entities from %d chunks", asset_name, len(all_entities), len(chunks))
            context.log.info(f"Extracted {len(all_entities)} entities from {len(chunks)} chunks")
            return Output(all_entities, metadata={"entity_count": len(all_entities)})

    return _ner_asset


# ── Proposition asset factory ────────────────────────────────────────

_SPO_SYSTEM_PROMPT = load_prompt(
    "propositions/spo",
    fallback=(
        "You are a knowledge-graph extraction system. "
        "Given a text chunk, extract Subject-Predicate-Object triples. "
        "Focus on factual, verifiable claims. Omit vague or opinion-based statements."
    ),
)


def make_proposition_asset(
    *,
    group_name: str,
    code_location: str,
    input_key: str,
    asset_name: str,
    layer: str = "gold",
):
    """Create an S-P-O proposition extraction asset for a given code location.

    Args:
        group_name: Dagster group (e.g. "congress", "leaks").
        code_location: Metrics label (e.g. "congress_data", "open_leaks").
        input_key: Name of the upstream chunks parameter (e.g. "congress_chunks").
        asset_name: Name for the generated asset (e.g. "congress_propositions").
        layer: EDC layer label for metadata.
    """
    tracer = get_tracer(f"{code_location}.{asset_name}")

    @asset(
        name=asset_name,
        group_name=group_name,
        description=f"Extract S-P-O propositions from {group_name} document chunks via LLM",
        compute_kind="llm",
        metadata={"layer": layer},
        op_tags=LLM_ASSET_K8S_CONFIG,
        ins={input_key: AssetIn()},
    )
    def _proposition_asset(
        context,
        llm: LLMResource,
        **kwargs: Any,
    ) -> Output[list[dict[str, Any]]]:
        chunks = kwargs[input_key]
        with trace_operation(
            asset_name, tracer, {"code_location": code_location, "layer": layer, "chunk_count": len(chunks)}
        ):
            logger.info("Starting %s extraction for %d chunks", asset_name, len(chunks))
            chain = llm.with_structured_output(PropositionResult)
            results = llm.invoke_batch(
                chain,
                lambda chunk: [
                    SystemMessage(content=_SPO_SYSTEM_PROMPT),
                    HumanMessage(
                        content=f"Extract subject-predicate-object propositions from this text:\n\n{chunk.text}"
                    ),
                ],
                chunks,
                operation="proposition_extract",
            )

            all_propositions: list[dict[str, Any]] = []
            for chunk, result in zip(chunks, results):
                for prop in result.propositions:
                    all_propositions.append({
                        **prop.model_dump(),
                        "source_doc_id": chunk.document_id,
                        "chunk_id": chunk.chunk_id,
                    })

            ASSET_RECORDS_PROCESSED.labels(
                code_location=code_location, asset_key=asset_name, layer=layer
            ).inc(len(all_propositions))
            logger.info(
                "%s complete: %d propositions from %d chunks",
                asset_name, len(all_propositions), len(chunks),
            )
            context.log.info(f"Extracted {len(all_propositions)} propositions from {len(chunks)} chunks")
            return Output(all_propositions, metadata={"proposition_count": len(all_propositions)})

    return _proposition_asset
