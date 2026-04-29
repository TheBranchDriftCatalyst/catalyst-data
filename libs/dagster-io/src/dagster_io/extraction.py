"""Validated extraction via LangGraph — shared helper for all code locations.

Wraps the catalyst-langgraph-aio extraction graph (extract → validate → repair)
and runs it per-chunk with concurrency. This replaces raw llm.invoke_batch() calls
so ALL extractions get MCP contract validation and span-checked repair.

Usage in Dagster assets:
    from dagster_io.extraction import extract_validated

    mentions, assertions = extract_validated(
        chunks=media_chunks,
        code_location="media_ingest",
        mention_prompt=MENTION_SYSTEM_PROMPT,
        assertion_prompt=ASSERTION_SYSTEM_PROMPT,
        max_concurrency=5,
    )
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor

from dagster_io.logging import get_logger
from dagster_io.metrics import (
    ENTITIES_EXTRACTED,
    LLM_REQUEST_DURATION,
    LLM_REQUESTS,
)

logger = get_logger(__name__)


def _build_pipeline_breakdown(audit_events: list[dict]) -> dict:
    """Parse audit events into a per-stage breakdown.

    Returns a dict with counts for each pipeline stage:
    - extract_mentions: how many times called, successes, errors
    - validate_mentions: verdicts (valid/ambiguous/invalid)
    - repair_mentions: attempts, successes, errors
    - extract_propositions / validate_propositions / repair_propositions: same
    - persist_artifacts / failure_handler: counts
    """
    stages: dict[str, dict] = {}
    for event in audit_events:
        node = event.get("node_name", "unknown")
        status = event.get("status", "unknown")
        details = event.get("details", {})

        if node not in stages:
            stages[node] = {"calls": 0, "completed": 0, "error": 0, "failed": 0}

        stages[node]["calls"] += 1
        if status in ("completed", "valid"):
            stages[node]["completed"] += 1
        elif status == "error":
            stages[node]["error"] += 1
        elif status in ("failed", "invalid"):
            stages[node]["failed"] += 1
        elif status == "ambiguous":
            stages[node].setdefault("ambiguous", 0)
            stages[node]["ambiguous"] += 1

        # Capture validation-specific details
        if "candidate_count" in details:
            stages[node].setdefault("total_candidates", 0)
            stages[node]["total_candidates"] += details["candidate_count"]
        if "errors" in details and isinstance(details["errors"], list):
            for err in details["errors"]:
                code = err.get("code", "unknown")
                stages[node].setdefault("error_codes", {})
                stages[node]["error_codes"][code] = stages[node]["error_codes"].get(code, 0) + 1

    return stages


def _build_graph():
    """Build the extraction graph — dispatches to v1 or v2.

    Set EXGRAPH_ENABLED=true to use the new catalyst-exgraph pipeline.
    Default (false) uses the original catalyst-langgraph-aio graph.
    Reads env var at call time so it can be toggled per-test.
    """
    if os.environ.get("EXGRAPH_ENABLED", "false").lower() == "true":
        return _build_graph_v2()
    return _build_graph_v1()


def _build_graph_v1():
    """Build the original hardcoded NER→SPO graph (catalyst-langgraph-aio).

    .. deprecated:: Use EXGRAPH_ENABLED=true for the new generic pipeline.
    """
    logger.info(
        "_build_graph_v1: using deprecated catalyst-langgraph-aio graph. "
        "Set EXGRAPH_ENABLED=true to use catalyst-exgraph."
    )
    from catalyst_contracts.validators.mention_validator import (
        validate_mentions as _validate_mentions,
    )
    from catalyst_contracts.validators.proposition_validator import (
        validate_propositions as _validate_propositions,
    )
    from catalyst_langgraph.clients.llm import LLMClient
    from catalyst_langgraph.clients.mcp import DirectMCPClient
    from catalyst_langgraph.graph import build_extraction_graph
    from catalyst_langgraph.repository.base import ArtifactRepository

    class _ValidatorHandler:
        """Direct handler for MCP validation — no subprocess needed."""

        def validate_mentions(self, mentions, source_text, document_id):
            result = _validate_mentions(mentions, source_text, document_id)
            return result.model_dump(mode="json")

        def validate_propositions(self, propositions, known_mention_ids, source_text):
            result = _validate_propositions(propositions, set(known_mention_ids), source_text)
            return result.model_dump(mode="json")

    class _NullRepository(ArtifactRepository):
        """No-op repository — Dagster's IO manager handles persistence."""

        async def save_mentions(self, document_id, mentions):
            pass

        async def save_propositions(self, document_id, propositions):
            pass

        async def save_audit_trail(self, document_id, audit_events):
            pass

        async def load_mentions(self, document_id):
            return []

        async def load_propositions(self, document_id):
            return []

    # Auto-detect specialized models and use their native adapters
    _llm_model_name = os.environ.get("LLM_MODEL", "")
    if "gliner" in _llm_model_name.lower():
        from catalyst_langgraph.clients.gliner import GLiNERClient

        llm_client = GLiNERClient()
    elif "nuextract" in _llm_model_name.lower():
        from catalyst_langgraph.clients.nuextract import NuExtractClient

        llm_client = NuExtractClient()
    elif "universalner" in _llm_model_name.lower() or "uniner" in _llm_model_name.lower():
        from catalyst_langgraph.clients.universalner import UniversalNERClient

        llm_client = UniversalNERClient()
    else:
        llm_client = LLMClient()

    mcp_client = DirectMCPClient(_ValidatorHandler())
    repo = _NullRepository()

    return build_extraction_graph(llm_client, mcp_client, repo), llm_client


def _build_graph_v2():
    """Build the new generic pipeline via catalyst-exgraph.

    Uses the same model detection logic as v1, but constructs a composable
    pipeline instead of a hardcoded NER→SPO graph. Returns (graph, client)
    with the same ainvoke() output shape as v1.
    """
    from catalyst_exgraph.config import ner_stage_config, spo_stage_config
    from catalyst_exgraph.dispatch import _LegacyAdapter
    from catalyst_exgraph.pipeline import build_pipeline
    from catalyst_exgraph.resource import _build_mcp_client, _resolve_client

    _llm_model_name = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    _llm_base_url = os.environ.get("LLM_BASE_URL")
    _llm_api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    logger.info("_build_graph_v2: model=%s, base_url=%s", _llm_model_name, _llm_base_url)
    client = _resolve_client(_llm_model_name, base_url=_llm_base_url, api_key=_llm_api_key)
    mcp_client = _build_mcp_client()

    is_encoder = any(x in _llm_model_name.lower() for x in ("gliner", "nuextract", "universalner", "uniner"))

    ner_config = ner_stage_config(model=_llm_model_name, max_retries=0 if is_encoder else 3)
    spo_config = spo_stage_config(model=_llm_model_name, max_retries=3)

    # Set prompt_dir if PROMPT_REGISTRY_DIR is set
    prompt_dir = os.environ.get("PROMPT_REGISTRY_DIR")
    if prompt_dir:
        from catalyst_exgraph.config import StageConfig

        ner_config = StageConfig(**{**ner_config.__dict__, "prompt_dir": prompt_dir})
        spo_config = StageConfig(**{**spo_config.__dict__, "prompt_dir": prompt_dir})

    pipeline = build_pipeline([ner_config, spo_config], client, mcp_client)

    logger.info("_build_graph_v2: using catalyst-exgraph pipeline (model=%s, encoder=%s)", _llm_model_name, is_encoder)
    return _LegacyAdapter(pipeline), client


async def _extract_chunk(graph, chunk_text: str, document_id: str, chunk_id: str, max_retries: int = 3) -> dict:
    """Run the full extraction graph on one chunk."""
    state = {
        "raw_text": chunk_text,
        "source_metadata": {
            "document_id": document_id,
            "chunk_id": chunk_id,
        },
        "max_retries": max_retries,
    }
    result = await graph.ainvoke(state)
    return {
        "mentions": result.get("accepted_mentions", []),
        "propositions": result.get("accepted_propositions", []),
        "status": result.get("status", "unknown"),
        "mention_retries": result.get("mention_retry_count", 0),
        "proposition_retries": result.get("proposition_retry_count", 0),
        "audit_events": result.get("audit_events", []),
    }


def extract_validated(
    chunks: list,
    code_location: str,
    *,
    max_concurrency: int = 5,
    max_retries: int = 3,
) -> tuple[list, list]:
    """Run validated extraction on a list of TextChunk objects.

    Uses the LangGraph extraction graph with MCP contract validation
    and repair cycles. Runs chunks concurrently.

    Domain-specific prompts are loaded automatically from PROMPT_REGISTRY_DIR
    (each Docker image bakes in shared + domain-specific .prompt files).

    Args:
        chunks: List of TextChunk objects (must have .text, .document_id, .chunk_id).
        code_location: For metrics labeling.
        max_concurrency: Max parallel extraction graphs.
        max_retries: Max repair attempts per chunk.

    Returns:
        (all_mentions, all_assertions) — flattened lists of Mention and
        Assertion domain model instances.
    """
    from dagster_io.models import Assertion, Mention, MentionType, Provenance

    if not chunks:
        return [], []

    _llm_model = os.environ.get("LLM_MODEL", "unknown")

    graph, llm_client = _build_graph()
    # Encoder/specialist models (GLiNER, NuExtract, UniversalNER) produce
    # deterministic output — repair loops are pointless. Skip retries.
    _is_encoder = getattr(llm_client, "structured_method", "") in ("gliner", "nuextract", "universalner")
    _max_retries = 0 if _is_encoder else max_retries
    all_mentions: list[dict] = []
    all_assertions: list[dict] = []
    all_audit_events: list[dict] = []
    completed = 0
    errors = 0
    total_mention_retries = 0
    total_proposition_retries = 0

    logger.info(
        "Starting validated extraction: %d chunks, concurrency=%d, code_location=%s",
        len(chunks),
        max_concurrency,
        code_location,
    )

    def _run_chunk(idx: int, chunk) -> tuple[int, dict]:
        """Run one chunk through the extraction graph (sync wrapper for async)."""
        loop = asyncio.new_event_loop()
        try:
            start = time.monotonic()
            result = loop.run_until_complete(
                _extract_chunk(graph, chunk.text, chunk.document_id, chunk.chunk_id, max_retries=_max_retries)
            )
            duration = time.monotonic() - start
            # Attach chunk metadata to result so we can build provenance later
            chunk_meta = getattr(chunk, "metadata", {}) or {}
            result["_chunk_metadata"] = chunk_meta
            result["_chunk_id"] = getattr(chunk, "chunk_id", "")
            LLM_REQUEST_DURATION.labels(model=_llm_model, operation="validated_extraction").observe(duration)
            LLM_REQUESTS.labels(model=_llm_model, operation="validated_extraction", status="success").inc()
            return idx, result
        except Exception as e:
            LLM_REQUESTS.labels(model=_llm_model, operation="validated_extraction", status="error").inc()
            logger.error("Extraction failed for chunk %d: %s", idx, e)
            raise
        finally:
            loop.close()

    from concurrent.futures import as_completed

    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {pool.submit(_run_chunk, i, chunk): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            idx, result = future.result()  # raises on permanent failure
            chunk_meta = result.get("_chunk_metadata", {})
            chunk_id = result.get("_chunk_id", "")
            # Tag each mention/assertion with its source chunk metadata
            for m in result["mentions"]:
                m["_chunk_metadata"] = chunk_meta
                m["_chunk_id"] = chunk_id
            for a in result["propositions"]:
                a["_chunk_metadata"] = chunk_meta
                a["_chunk_id"] = chunk_id
            all_mentions.extend(result["mentions"])
            all_assertions.extend(result["propositions"])
            all_audit_events.extend(result.get("audit_events", []))
            total_mention_retries += result["mention_retries"]
            total_proposition_retries += result["proposition_retries"]

            if result["status"] == "failed":
                errors += 1

            completed += 1
            if completed % 50 == 0 or completed == len(chunks):
                logger.info(
                    "Validated extraction progress: %d/%d (%.0f%%)%s",
                    completed,
                    len(chunks),
                    completed / len(chunks) * 100,
                    f" ({errors} failures)" if errors else "",
                )

    # Convert raw dicts to domain models
    mention_models = []
    for m in all_mentions:
        mention_type_str = m.get("mention_type", "OTHER")
        try:
            mention_type = MentionType(mention_type_str)
        except ValueError:
            mention_type = MentionType.OTHER

        ENTITIES_EXTRACTED.labels(
            code_location=code_location,
            entity_type=mention_type.value,
            method="langgraph_validated",
        ).inc()

        # Build provenance from chunk metadata (temporal + speaker data)
        chunk_meta = m.pop("_chunk_metadata", {})
        chunk_id_from_meta = m.pop("_chunk_id", "")
        prov = None
        start_s = chunk_meta.get("start_s")
        end_s = chunk_meta.get("end_s")
        speaker = chunk_meta.get("speaker")
        if start_s is not None or speaker:
            prov = Provenance(
                source_document_id=m.get("document_id", ""),
                chunk_id=chunk_id_from_meta or m.get("chunk_id", ""),
                temporal_start_ms=int(start_s * 1000) if start_s is not None else None,
                temporal_end_ms=int(end_s * 1000) if end_s is not None else None,
                speaker_label=speaker,
                extraction_method="llm",
            )

        mention_models.append(
            Mention(
                document_id=m.get("document_id", ""),
                chunk_id=chunk_id_from_meta or m.get("chunk_id", ""),
                text=m.get("text", ""),
                mention_type=mention_type,
                span_start=m.get("span_start"),
                span_end=m.get("span_end"),
                confidence=m.get("confidence", 1.0),
                context=m.get("context", ""),
                provenance=prov,
            )
        )

    assertion_models = []
    for a in all_assertions:
        chunk_meta = a.pop("_chunk_metadata", {})
        chunk_id_from_meta = a.pop("_chunk_id", "")
        a_prov = None
        a_start_s = chunk_meta.get("start_s")
        a_end_s = chunk_meta.get("end_s")
        a_speaker = chunk_meta.get("speaker")
        if a_start_s is not None or a_speaker:
            a_prov = Provenance(
                source_document_id=a.get("document_id", ""),
                chunk_id=chunk_id_from_meta,
                temporal_start_ms=int(a_start_s * 1000) if a_start_s is not None else None,
                temporal_end_ms=int(a_end_s * 1000) if a_end_s is not None else None,
                speaker_label=a_speaker,
                extraction_method="llm",
            )

        assertion_models.append(
            Assertion(
                subject_text=a.get("subject", a.get("subject_text", "")),
                predicate=a.get("predicate", ""),
                object_text=a.get("object", a.get("object_text", "")),
                confidence=a.get("confidence", 1.0),
                negated=a.get("negated", False),
                hedged=a.get("hedged", False),
                qualifiers=a.get("qualifiers", {}),
                provenance=a_prov,
            )
        )

    logger.info(
        "Validated extraction complete: %d mentions, %d assertions from %d chunks "
        "(%d mention retries, %d proposition retries, %d failures)",
        len(mention_models),
        len(assertion_models),
        len(chunks),
        total_mention_retries,
        total_proposition_retries,
        errors,
    )

    # Build pipeline breakdown from audit events
    pipeline_breakdown = _build_pipeline_breakdown(all_audit_events)

    # Stash stats for callers that need them (e.g. benchmark tests).
    # Does not change the return signature — production assets are unaffected.
    extract_validated.last_stats = {
        "chunk_count": len(chunks),
        "mention_count": len(mention_models),
        "assertion_count": len(assertion_models),
        "mention_retries": total_mention_retries,
        "proposition_retries": total_proposition_retries,
        "errors": errors,
        "pipeline": pipeline_breakdown,
        "audit_events": all_audit_events,
    }

    return mention_models, assertion_models
