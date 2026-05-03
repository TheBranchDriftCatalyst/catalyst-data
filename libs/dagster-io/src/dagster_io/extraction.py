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
    """Build the extraction graph via catalyst-exgraph.

    The legacy v1 catalyst-langgraph-aio graph + the EXGRAPH_ENABLED toggle
    were removed when ExGraph became the only supported pipeline (CD-ys8n).
    """
    from catalyst_exgraph.config import StageConfig, ner_stage_config, spo_stage_config
    from catalyst_exgraph.pipeline import build_pipeline
    from catalyst_exgraph.resource import _build_mcp_client, _resolve_client

    _llm_model_name = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    _llm_base_url = os.environ.get("LLM_BASE_URL")
    _llm_api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    logger.info("_build_graph: model=%s, base_url=%s", _llm_model_name, _llm_base_url)
    client = _resolve_client(_llm_model_name, base_url=_llm_base_url, api_key=_llm_api_key)
    mcp_client = _build_mcp_client()

    is_encoder = any(x in _llm_model_name.lower() for x in ("gliner", "nuextract", "universalner", "uniner"))

    ner_config = ner_stage_config(model=_llm_model_name, max_retries=0 if is_encoder else 3)
    spo_config = spo_stage_config(model=_llm_model_name, max_retries=3, skip=is_encoder)

    prompt_dir = os.environ.get("PROMPT_REGISTRY_DIR")
    if prompt_dir:
        ner_config = StageConfig(**{**ner_config.__dict__, "prompt_dir": prompt_dir})
        spo_config = StageConfig(**{**spo_config.__dict__, "prompt_dir": prompt_dir})

    from dagster_io.chunking import ChunkConfig

    context_window = int(os.environ.get("LLM_CONTEXT_WINDOW", "4096"))
    chunk_config = ChunkConfig(model_context_tokens=context_window)

    pipeline = build_pipeline([ner_config, spo_config], client, mcp_client, chunk_config=chunk_config)

    logger.info(
        "_build_graph: catalyst-exgraph pipeline (model=%s, encoder=%s, context_window=%d)",
        _llm_model_name,
        is_encoder,
        context_window,
    )
    # The exgraph pipeline exposes an ``ainvoke`` returning ExGraphState; the
    # extract/dispatch shim used to wrap it in _LegacyAdapter to flatten the
    # output. We now flatten in _extract_chunk directly so there's no shim.
    return pipeline, client


async def _extract_chunk(
    graph,
    chunk_text: str,
    document_id: str,
    chunk_id: str,
    max_retries: int = 3,
    *,
    chunk_index: int | None = None,
    total_chunks: int | None = None,
    chunk_metadata: dict | None = None,
) -> dict:
    """Run the full extraction graph on one chunk.

    ``graph`` is the catalyst-exgraph compiled pipeline. We flatten its
    ExGraphState output via ``pipeline_result_to_legacy`` so callers see
    the flat ``accepted_mentions`` / ``accepted_propositions`` / ``status``
    shape they've always consumed — no LegacyAdapter wrapper needed.

    chunk_index / total_chunks / chunk_metadata flow into source_metadata
    so the ExtractNode can surface them on the chunk_loaded event for the
    StateInspector's right-pane chunking-strategy view.
    """
    from catalyst_exgraph.pipeline import pipeline_result_to_legacy

    # CATALYST_BENCH_MODEL is the human-readable bench config name set by
    # the harness (e.g. 'gliner-medium'); LLM_MODEL is the underlying model
    # id (e.g. 'urchade/gliner_medium-v2.1'). Prefer the bench name so the
    # StateInspector dropdown shows the same labels as benchmark_config.py.
    bench_model = os.environ.get("CATALYST_BENCH_MODEL") or os.environ.get("LLM_MODEL", "")
    cm = chunk_metadata or {}
    state = {
        "raw_text": chunk_text,
        "source_metadata": {
            "document_id": document_id,
            "chunk_id": chunk_id,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "chunk_metadata": cm,
            # Promote a few hot fields the ExtractNode already reads so
            # the existing emit_chunk_text call sites keep working.
            "domain": cm.get("domain"),
            "speaker_label": cm.get("speaker"),
            "temporal_start_ms": (cm.get("start_s") * 1000) if cm.get("start_s") is not None else None,
            "temporal_end_ms": (cm.get("end_s") * 1000) if cm.get("end_s") is not None else None,
        },
        "max_retries": max_retries,
        # Threaded into the pipeline state so emit_chunk_extracted_for_state
        # can tag chunk_extracted events with the bench model — without this,
        # all events have model=null and the StateInspector can't filter.
        "model": bench_model,
        "doc_id": document_id,
        "chunk_id": chunk_id,
    }
    raw_result = await graph.ainvoke(state)
    result = pipeline_result_to_legacy(raw_result)
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
                _extract_chunk(
                    graph,
                    chunk.text,
                    chunk.document_id,
                    chunk.chunk_id,
                    max_retries=_max_retries,
                    chunk_index=getattr(chunk, "index", None),
                    total_chunks=getattr(chunk, "total_chunks", None),
                    chunk_metadata=getattr(chunk, "metadata", {}) or {},
                )
            )
            duration = time.monotonic() - start
            # Attach chunk metadata to result so we can build provenance later.
            # Carry chunk.text + chunk.document_id explicitly so post-extraction
            # context computation and document_id fallback work without a second
            # join against the chunk list.
            chunk_meta = getattr(chunk, "metadata", {}) or {}
            result["_chunk_metadata"] = chunk_meta
            result["_chunk_id"] = getattr(chunk, "chunk_id", "")
            result["_chunk_text"] = getattr(chunk, "text", "") or ""
            result["_chunk_document_id"] = getattr(chunk, "document_id", "") or ""
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
            chunk_text = result.get("_chunk_text", "")
            chunk_doc_id = result.get("_chunk_document_id", "")
            # Tag each mention/assertion with its source chunk metadata
            for m in result["mentions"]:
                m["_chunk_metadata"] = chunk_meta
                m["_chunk_id"] = chunk_id
                m["_chunk_text"] = chunk_text
                m["_chunk_document_id"] = chunk_doc_id
            for a in result["propositions"]:
                a["_chunk_metadata"] = chunk_meta
                a["_chunk_id"] = chunk_id
                a["_chunk_document_id"] = chunk_doc_id
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

        # Build provenance — ALWAYS, not just for temporal/speaker data
        chunk_meta = m.pop("_chunk_metadata", {})
        chunk_id_from_meta = m.pop("_chunk_id", "")
        chunk_text = m.pop("_chunk_text", "")
        chunk_doc_id = m.pop("_chunk_document_id", "")
        # Prefer the chunk's document_id (always present, set by chunker) over
        # whatever the LLM may or may not have echoed back.
        doc_id = chunk_doc_id or m.get("document_id", "")
        cid = chunk_id_from_meta or m.get("chunk_id", "")
        start_s = chunk_meta.get("start_s")
        end_s = chunk_meta.get("end_s")
        speaker = chunk_meta.get("speaker")
        # Compute context window from chunk text + spans (LLM rarely returns it).
        # Window = ±100 chars around the mention span; falls back to LLM-supplied
        # context if present, then to empty string.
        computed_context = ""
        sp = m.get("span_start")
        ep = m.get("span_end")
        if chunk_text and sp is not None and ep is not None and 0 <= sp < ep <= len(chunk_text):
            ctx_start = max(0, sp - 100)
            ctx_end = min(len(chunk_text), ep + 100)
            computed_context = chunk_text[ctx_start:ctx_end]

        prov = Provenance(
            source_document_id=doc_id,
            chunk_id=cid,
            span_start=m.get("span_start"),
            span_end=m.get("span_end"),
            temporal_start_ms=int(start_s * 1000) if start_s is not None else None,
            temporal_end_ms=int(end_s * 1000) if end_s is not None else None,
            speaker_label=speaker,
            extraction_method="llm",
            extraction_model=_llm_model,
            code_location=code_location,
        )

        mention_models.append(
            Mention(
                document_id=doc_id,
                chunk_id=cid,
                text=m.get("text", ""),
                mention_type=mention_type,
                span_start=m.get("span_start"),
                span_end=m.get("span_end"),
                confidence=m.get("confidence", 1.0),
                context=m.get("context") or computed_context,
                provenance=prov,
            )
        )

    # Build mention lookup for assertion ↔ mention linkage
    # Key: (chunk_id, normalized_text) → mention_id
    _mention_index: dict[tuple[str, str], str] = {}
    for mm in mention_models:
        _mention_index[(mm.chunk_id, mm.text.strip().lower())] = mm.mention_id

    assertion_models = []
    for a in all_assertions:
        chunk_meta = a.pop("_chunk_metadata", {})
        chunk_id_from_meta = a.pop("_chunk_id", "")
        a_chunk_doc_id = a.pop("_chunk_document_id", "")
        a_doc_id = a_chunk_doc_id or a.get("document_id", "")
        a_cid = chunk_id_from_meta or ""
        a_start_s = chunk_meta.get("start_s")
        a_end_s = chunk_meta.get("end_s")
        a_speaker = chunk_meta.get("speaker")

        a_prov = Provenance(
            source_document_id=a_doc_id,
            chunk_id=a_cid,
            temporal_start_ms=int(a_start_s * 1000) if a_start_s is not None else None,
            temporal_end_ms=int(a_end_s * 1000) if a_end_s is not None else None,
            speaker_label=a_speaker,
            extraction_method="llm",
            extraction_model=_llm_model,
            code_location=code_location,
        )

        subj_text = a.get("subject") or a.get("subject_text") or ""
        obj_text = a.get("object") or a.get("object_text") or ""

        # Link to mention IDs if matching mentions exist in the same chunk
        subj_mid = _mention_index.get((a_cid, subj_text.strip().lower()), "")
        obj_mid = _mention_index.get((a_cid, obj_text.strip().lower()), "")

        assertion_models.append(
            Assertion(
                subject_text=subj_text,
                subject_mention_id=subj_mid,
                predicate=a.get("predicate", ""),
                object_text=obj_text,
                object_mention_id=obj_mid,
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
    # Include chunk_config info if exgraph pipeline was used
    _context_window = int(os.environ.get("LLM_CONTEXT_WINDOW", "4096"))

    # Total LLM calls across the run. Validators are deterministic (no LLM),
    # so we count only the four LLM-calling LangGraph nodes. Two-tier:
    #   1. Audit events (exact, when audit logging is on)
    #   2. Derived from chunks + retries (when audit isn't captured)
    # Encoder runs (e.g. gliner) bypass this code path entirely; they end up
    # at 0 LLM calls here and the harness reports inference_calls separately.
    _LLM_NODES = {
        "mention_extractor",
        "repair_mention_extractor",
        "proposition_extractor",
        "repair_proposition_extractor",
    }
    audit_call_count = sum(1 for e in all_audit_events if e.get("node_name") in _LLM_NODES)
    derived_call_count = (
        completed * 2  # base: 1 NER + 1 SPO per successful chunk
        + total_mention_retries
        + total_proposition_retries
    )
    llm_call_count = audit_call_count or derived_call_count

    extract_validated.last_stats = {
        "chunk_count": len(chunks),
        "mention_count": len(mention_models),
        "assertion_count": len(assertion_models),
        "mention_retries": total_mention_retries,
        "proposition_retries": total_proposition_retries,
        "errors": errors,
        "llm_call_count": llm_call_count,
        "pipeline": pipeline_breakdown,
        "audit_events": all_audit_events,
        "context_window": _context_window,
    }

    return mention_models, assertion_models
