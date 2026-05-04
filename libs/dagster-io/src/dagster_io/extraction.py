"""Validated extraction via LangGraph — shared helper for all code locations.

Phase 2 (CD-j6d3): entity-anchored flow.

Flow per doc:
    chunks → doc grouping → NER once per doc → cluster_entities → pack_evidence
    → SPO fan-out per evidence window → persist

The outer driver ``extract_validated`` groups input chunks by ``document_id``,
runs a single NER pass per doc (so clustering is done on the full doc entity
set rather than per-chunk), then fans out SPO extraction once per evidence window.

Usage in Dagster assets:
    from dagster_io.extraction import extract_validated

    mentions, assertions = extract_validated(
        chunks=media_chunks,
        code_location="media_ingest",
        max_concurrency=5,
    )
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from dagster_io.logging import get_logger
from dagster_io.metrics import (
    ENTITIES_EXTRACTED,
    LLM_REQUEST_DURATION,
    LLM_REQUESTS,
)

logger = get_logger(__name__)


def _per_call_timeout_s() -> float:
    """Per-LLM-call wall-clock cap (CD-azmn).

    Read once per call from ``LLM_PER_CALL_TIMEOUT`` (seconds, default 600).
    The httpx read-timeout in LLMClient already bounds a single HTTP read,
    but the SPO pipeline can issue *multiple* reads (extract + repair, +
    validators) for one ainvoke. This wrapper bounds the whole evidence-window
    invocation so a wedged Ollama can't eat hours of the bench.
    """
    try:
        return float(os.environ.get("LLM_PER_CALL_TIMEOUT", "600"))
    except (TypeError, ValueError):
        return 600.0


@dataclass
class _Doc:
    """A reconstructed document from one or more chunks."""

    doc_id: str
    full_text: str
    chunks: list  # original TextChunk objects in index order
    chunk_metadata: dict  # metadata from first chunk (representative)


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


def _group_chunks_into_docs(chunks: list) -> list[_Doc]:
    """Group input chunks by ``document_id``, concatenate text in chunk-index order.

    Each ``_Doc`` contains the full concatenated text for the document so that
    NER runs once on the complete doc rather than per-chunk.

    If a chunk has no ``document_id``, it is treated as its own single-chunk doc.
    """
    from collections import defaultdict

    groups: dict[str, list] = defaultdict(list)
    for chunk in chunks:
        doc_id = getattr(chunk, "document_id", None) or "unknown"
        groups[doc_id].append(chunk)

    docs: list[_Doc] = []
    for doc_id, doc_chunks in groups.items():
        # Sort by chunk index (if available)
        sorted_chunks = sorted(doc_chunks, key=lambda c: getattr(c, "index", 0))
        full_text = "\n\n".join(getattr(c, "text", "") or "" for c in sorted_chunks)
        first_meta = getattr(sorted_chunks[0], "metadata", {}) or {}
        docs.append(_Doc(doc_id=doc_id, full_text=full_text, chunks=sorted_chunks, chunk_metadata=first_meta))

    return docs


def _build_pipelines():
    """Build the NER pipeline, SPO pipeline, LLM client, and optional embedder.

    Phase 2 (CD-j6d3): returns (ner_pipeline, spo_pipeline, client, embedder).
    The NER pipeline runs cluster+pack after NER.  The SPO pipeline runs
    per evidence window.
    """
    from catalyst_exgraph.config import StageConfig, ner_stage_config, spo_stage_config
    from catalyst_exgraph.pipeline import build_ner_pipeline, build_spo_pipeline
    from catalyst_exgraph.resource import _build_mcp_client, _resolve_client

    _llm_model_name = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    _llm_base_url = os.environ.get("LLM_BASE_URL")
    _llm_api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    logger.info("_build_pipelines: model=%s, base_url=%s", _llm_model_name, _llm_base_url)
    client = _resolve_client(_llm_model_name, base_url=_llm_base_url, api_key=_llm_api_key)
    mcp_client = _build_mcp_client()

    is_encoder = any(x in _llm_model_name.lower() for x in ("gliner", "nuextract", "universalner", "uniner"))

    ner_config = ner_stage_config(model=_llm_model_name, max_retries=0 if is_encoder else 3)
    spo_config = spo_stage_config(model=_llm_model_name, max_retries=3, skip=is_encoder)

    prompt_dir = os.environ.get("PROMPT_REGISTRY_DIR")
    if prompt_dir:
        ner_config = StageConfig(**{**ner_config.__dict__, "prompt_dir": prompt_dir})
        spo_config = StageConfig(**{**spo_config.__dict__, "prompt_dir": prompt_dir})

    # Optional embedder for cluster merge. Default is Ollama at localhost:11434
    # with qwen3-embedding:8b — the canonical local rig (mac-node). Falls back
    # to proximity-only clustering when the endpoint is unreachable or the
    # model isn't pulled. Override via EMBED_PROVIDER + EMBED_MODEL +
    # EMBED_BASE_URL.
    embedder = None
    _embed_provider = os.environ.get("EMBED_PROVIDER", "ollama")
    if _embed_provider and _embed_provider != "none":
        try:
            from dagster_io.llm import EmbeddingResource

            _embed_model = os.environ.get("EMBED_MODEL", "qwen3-embedding:8b")
            _embed_base_url = os.environ.get("EMBED_BASE_URL")
            kwargs = {"provider": _embed_provider, "model": _embed_model, "enable_cache": True}
            if _embed_base_url:
                kwargs["base_url"] = _embed_base_url
            embedder = EmbeddingResource(**kwargs)
            # EmbeddingResource is a ConfigurableResource whose backend load
            # happens in setup_for_execution(). When called outside a Dagster
            # execution context (e.g. bench harness) we must invoke it manually.
            embedder.setup_for_execution(None)
            # Sanity check: provider="local" and "huggingface" set _st_model;
            # all HTTP-backed providers set _embeddings. If neither was assigned,
            # the silent failure path bites later when embed() is called.
            if embedder._st_model is None and embedder._embeddings is None:
                raise RuntimeError(
                    f"EmbeddingResource setup_for_execution did not assign a backend "
                    f"(provider={_embed_provider!r}, model={_embed_model!r})"
                )
            logger.info(
                "_build_pipelines: embedding merge enabled, provider=%s model=%s",
                _embed_provider,
                _embed_model,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("_build_pipelines: embedder not available (%s) — proximity-only clustering", exc)
            embedder = None

    ner_pipeline = build_ner_pipeline(ner_config, client, mcp_client, embedder=embedder)
    spo_pipeline = build_spo_pipeline(spo_config, client, mcp_client)

    logger.info(
        "_build_pipelines: ner+cluster+pack pipeline + spo pipeline (model=%s, encoder=%s)",
        _llm_model_name,
        is_encoder,
    )
    return ner_pipeline, spo_pipeline, client, embedder


# Keep _build_graph for callers that still use the legacy combined pipeline path.
# Deprecated — Phase 4 cleanup (CD-j6d3 follow-up) will remove.
def _build_graph():
    """DEPRECATED — use ``_build_pipelines()`` instead."""
    from catalyst_exgraph.config import StageConfig, ner_stage_config, spo_stage_config
    from catalyst_exgraph.pipeline import build_pipeline
    from catalyst_exgraph.resource import _build_mcp_client, _resolve_client

    _llm_model_name = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    _llm_base_url = os.environ.get("LLM_BASE_URL")
    _llm_api_key = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
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
    return pipeline, client


async def _process_doc(
    ner_pipeline,
    spo_pipeline,
    doc: "_Doc",
    bench_model: str,
    max_retries: int = 3,
) -> dict:
    """Process one document end-to-end: NER → cluster → pack → SPO fan-out.

    Returns a dict with:
        mentions: list[dict]
        propositions: list[dict]
        audit_events: list[dict]
        mention_retries: int
        proposition_retries: int
        status: str
    """
    cm = doc.chunk_metadata or {}
    first_chunk = doc.chunks[0] if doc.chunks else None
    first_chunk_id = getattr(first_chunk, "chunk_id", "") if first_chunk else ""

    # ── NER pass — once per doc ───────────────────────────────────────────
    ner_state_input = {
        "raw_text": doc.full_text,
        "doc_id": doc.doc_id,
        "model": bench_model,
        "source_metadata": {
            "document_id": doc.doc_id,
            "chunk_id": first_chunk_id,
            "chunk_index": 0,
            "total_chunks": len(doc.chunks),
            "chunk_metadata": cm,
            "domain": cm.get("domain"),
            "speaker_label": cm.get("speaker"),
            "temporal_start_ms": (cm.get("start_s") * 1000) if cm.get("start_s") is not None else None,
            "temporal_end_ms": (cm.get("end_s") * 1000) if cm.get("end_s") is not None else None,
        },
        "max_retries": max_retries,
    }
    ner_result = await ner_pipeline.ainvoke(ner_state_input)

    accepted_mentions: list[dict] = (ner_result.get("stages") or {}).get("ner", {}).get("accepted") or []
    evidence_windows: list[dict] = ner_result.get("evidence_windows") or []
    ner_audit: list[dict] = ner_result.get("audit_events") or []
    ner_retries = (ner_result.get("stages") or {}).get("ner", {}).get("retry_count", 0)

    all_propositions: list[dict] = []
    spo_audit: list[dict] = []
    spo_retries_total = 0
    spo_status = "completed"

    # ── SPO fan-out per evidence window ───────────────────────────────────
    # Prefer consensus_mentions (Phase B) when available — they carry full
    # provenance (vote_count, n_encoders, mean_confidence) that the SPO
    # prompt uses to weight entity reliability.  Fall back to the NER-stage
    # accepted list for legacy single-NER pipelines.
    consensus_mentions: list[dict] = ner_result.get("consensus_mentions") or []

    n_windows = len(evidence_windows)
    if n_windows == 0:
        logger.info("[%s] %s: 0 evidence windows — skipping SPO", bench_model, doc.doc_id)
    else:
        logger.info(
            "[%s] %s: %d evidence window%s, %d consensus mentions",
            bench_model,
            doc.doc_id,
            n_windows,
            "" if n_windows == 1 else "s",
            len(consensus_mentions) if consensus_mentions else len(accepted_mentions),
        )

    for w_i, window in enumerate(evidence_windows, start=1):
        window_id = window.get("window_id", "")
        mention_indices: list[int] = window.get("mention_indices") or []
        if consensus_mentions:
            # consensus_mentions are indexed in parallel with accepted_mentions
            # (both lists are built from the same NER accepted set, with the
            # same ordering).  Use the same mention_indices to slice.
            window_mentions = [consensus_mentions[i] for i in mention_indices if i < len(consensus_mentions)]
        else:
            window_mentions = [accepted_mentions[i] for i in mention_indices if i < len(accepted_mentions)]

        # Use the evidence-window id as the chunk_id for SPO events so the
        # StateInspector rail surfaces one card per window (the right
        # granularity now that NER is doc-scoped). Mirror evidence_window_id
        # into source_metadata too — LangGraph's state propagation drops
        # top-level fields it didn't see in the initial schema, but nested
        # dict values survive.
        window_chunk_id = f"{doc.doc_id}:{window_id}" if window_id else f"{doc.doc_id}:_unwindowed"
        spo_state_input = {
            "raw_text": window.get("text", ""),
            "doc_id": doc.doc_id,
            "evidence_window_id": window_id,
            "chunk_id": window_chunk_id,
            "model": bench_model,
            "source_metadata": {
                "document_id": doc.doc_id,
                "chunk_id": window_chunk_id,
                "evidence_window_id": window_id,
                "chunk_index": 0,
                "total_chunks": len(doc.chunks),
                "chunk_metadata": cm,
                "domain": cm.get("domain"),
                "speaker_label": cm.get("speaker"),
                "temporal_start_ms": (cm.get("start_s") * 1000) if cm.get("start_s") is not None else None,
                "temporal_end_ms": (cm.get("end_s") * 1000) if cm.get("end_s") is not None else None,
            },
            "upstream_context": {"accepted_mentions": window_mentions},
            "stages": ner_result.get("stages", {}),
            "max_retries": max_retries,
        }
        # CD-azmn: bound each evidence-window SPO invocation in wall-clock
        # so a wedged Ollama trips here instead of hanging the whole bench.
        win_t0 = time.perf_counter()
        spo_result = await asyncio.wait_for(spo_pipeline.ainvoke(spo_state_input), timeout=_per_call_timeout_s())
        win_dt = time.perf_counter() - win_t0

        spo_accepted: list[dict] = (spo_result.get("stages") or {}).get("spo", {}).get("accepted") or []
        all_propositions.extend(spo_accepted)
        spo_audit.extend(spo_result.get("audit_events") or [])
        win_retries = (spo_result.get("stages") or {}).get("spo", {}).get("retry_count", 0)
        spo_retries_total += win_retries
        if spo_result.get("status") == "failed":
            spo_status = "failed"

        logger.info(
            "[%s] %s win %d/%d  %d props · %.1fs%s",
            bench_model,
            doc.doc_id,
            w_i,
            n_windows,
            len(spo_accepted),
            win_dt,
            f" · {win_retries} retries" if win_retries else "",
        )

        # Emit chunk_extracted per window so the State Inspector OUTPUT pane
        # shows per-window SPO propositions for v4 win-N chunk_ids.
        from dagster_io import event_tail as _et

        if _et.is_configured():
            _et.emit_chunk_extracted(
                window_chunk_id,
                model=bench_model,
                doc_id=doc.doc_id,
                mentions=window_mentions,
                propositions=spo_accepted,
            )

    # Determine overall status
    status = "failed" if ner_result.get("status") == "failed" or spo_status == "failed" else "completed"

    return {
        "mentions": accepted_mentions,
        "propositions": all_propositions,
        "audit_events": ner_audit + spo_audit,
        "mention_retries": ner_retries,
        "proposition_retries": spo_retries_total,
        "status": status,
        "_doc_id": doc.doc_id,
        "_chunk_metadata": cm,
        "_chunk_id": first_chunk_id,
        "_chunk_text": doc.full_text,
        "_chunk_document_id": doc.doc_id,
    }


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


async def _process_doc_spo_only(
    spo_pipeline,
    doc: "_Doc",
    cached_clusters: list,
    bench_model: str,
    max_retries: int = 3,
    doc_mentions: "list[dict] | None" = None,
) -> dict:
    """Run SPO fan-out only for a doc using pre-computed clusters.

    Mirrors ``_process_doc`` but skips the NER pass entirely — clusters are
    provided from the caller's cache so this is cheap (no embedding, no NER).
    Returns the same dict shape as ``_process_doc`` with an empty
    ``mentions`` list (the caller accumulates those from the shared NER pass).

    Phase 3 (CD-80ic).

    Args:
        doc_mentions: Optional list of pre-computed mention dicts for this doc
            (from Phase A's shared NER pass).  When provided, window mentions
            are sliced from this list using ``mention_indices`` from each
            evidence window, giving the SPO LLM the per-entity provenance
            metadata (vote_count, mean_confidence) it needs to weight entity
            reliability.  Falls back to empty list when ``None``.
    """
    cm = doc.chunk_metadata or {}
    first_chunk = doc.chunks[0] if doc.chunks else None
    first_chunk_id = getattr(first_chunk, "chunk_id", "") if first_chunk else ""

    # Reconstruct evidence windows from clusters for the *target* model.
    # We do this by running the pack_evidence node with the target model name
    # so window sizes reflect the current target model's context budget.
    from catalyst_exgraph.nodes.pack import PackEvidenceNode

    pack_node = PackEvidenceNode()
    pack_state: dict = {
        "raw_text": doc.full_text,
        "doc_id": doc.doc_id,
        "model": bench_model,
        "entity_clusters": cached_clusters,
        "stages": {},
        "audit_events": [],
    }
    pack_result = await pack_node(pack_state)
    evidence_windows: list[dict] = pack_result.get("evidence_windows") or []

    all_propositions: list[dict] = []
    spo_audit: list[dict] = []
    spo_retries_total = 0
    spo_status = "completed"

    # Pre-built list of mentions for this doc (may carry ConsensusMention fields).
    _doc_mentions: list[dict] = doc_mentions or []

    for window in evidence_windows:
        window_id = window.get("window_id", "")
        mention_indices: list[int] = window.get("mention_indices") or []
        # Per-window chunk_id so the StateInspector rail surfaces one card
        # per evidence window (the right granularity for v3); evidence_window_id
        # mirrored into source_metadata to survive LangGraph state filtering.
        window_chunk_id = f"{doc.doc_id}:{window_id}" if window_id else f"{doc.doc_id}:_unwindowed"
        # Slice mentions for this window — preserves ConsensusMention provenance
        # fields (vote_count, mean_confidence) so the SPO LLM can weight
        # entity reliability.  Falls back to empty list if doc_mentions wasn't
        # provided (legacy callers).
        window_mentions = [_doc_mentions[i] for i in mention_indices if i < len(_doc_mentions)]
        spo_state_input = {
            "raw_text": window.get("text", ""),
            "doc_id": doc.doc_id,
            "evidence_window_id": window_id,
            "chunk_id": window_chunk_id,
            "model": bench_model,
            "source_metadata": {
                "document_id": doc.doc_id,
                "chunk_id": window_chunk_id,
                "evidence_window_id": window_id,
                "chunk_index": 0,
                "total_chunks": len(doc.chunks),
                "chunk_metadata": cm,
                "domain": cm.get("domain"),
                "speaker_label": cm.get("speaker"),
                "temporal_start_ms": (cm.get("start_s") * 1000) if cm.get("start_s") is not None else None,
                "temporal_end_ms": (cm.get("end_s") * 1000) if cm.get("end_s") is not None else None,
            },
            "upstream_context": {"accepted_mentions": window_mentions},
            "stages": {},
            "max_retries": max_retries,
        }
        # CD-azmn: bound each evidence-window SPO invocation in wall-clock.
        spo_result = await asyncio.wait_for(spo_pipeline.ainvoke(spo_state_input), timeout=_per_call_timeout_s())

        spo_accepted: list[dict] = (spo_result.get("stages") or {}).get("spo", {}).get("accepted") or []
        all_propositions.extend(spo_accepted)
        spo_audit.extend(spo_result.get("audit_events") or [])
        spo_retries_total += (spo_result.get("stages") or {}).get("spo", {}).get("retry_count", 0)
        if spo_result.get("status") == "failed":
            spo_status = "failed"

        # Emit chunk_extracted per window (SPO-only path mirrors _process_doc).
        from dagster_io import event_tail as _et

        if _et.is_configured():
            _et.emit_chunk_extracted(
                window_chunk_id,
                model=bench_model,
                doc_id=doc.doc_id,
                mentions=window_mentions,
                propositions=spo_accepted,
            )

    return {
        "mentions": [],  # caller provides shared NER mentions
        "propositions": all_propositions,
        "audit_events": spo_audit,
        "mention_retries": 0,
        "proposition_retries": spo_retries_total,
        "status": "failed" if spo_status == "failed" else "completed",
        "_doc_id": doc.doc_id,
        "_chunk_metadata": cm,
        "_chunk_id": first_chunk_id,
        "_chunk_text": doc.full_text,
        "_chunk_document_id": doc.doc_id,
    }


def extract_with_shared_clusters(
    docs: list["_Doc"],
    cached_clusters: "dict[str, list]",
    *,
    shared_mentions: "dict[str, list[dict]] | None" = None,
    code_location: str,
    max_concurrency: int = 5,
    max_retries: int = 3,
) -> "tuple[list, list]":
    """Run SPO fan-out per cached cluster for the current LLM_MODEL — skips NER.

    Phase 3 entry point for the bench harness two-phase flow (CD-80ic).
    NER + clustering has already been performed (Phase A); this function only
    runs the cheap ``pack_evidence → SPO fan-out`` step per doc using the
    caller-supplied cluster cache.

    Args:
        docs: ``_Doc`` objects produced by ``_group_chunks_into_docs``.
        cached_clusters: ``{doc_id: list[EntityCluster]}`` from Phase A.
            Docs without an entry in this dict are silently skipped (no SPO
            output for that doc).
        shared_mentions: ``{doc_id: list[mention_dict]}`` from Phase A's NER
            pass.  When provided, these mentions are converted to ``Mention``
            domain models and included in the returned mention list so that
            all Phase B models report the Phase A NER mentions.  When ``None``
            (legacy callers), an empty mention list is returned.  (CD-7bco)
        code_location: For metrics labeling.
        max_concurrency: Max parallel doc-level SPO tasks.
        max_retries: Max repair attempts per SPO stage per evidence window.

    Returns:
        ``(mention_models, assertion_models)`` — mentions are the Phase A NER
        output (converted to domain models) when ``shared_mentions`` is
        provided; otherwise an empty list.
    """
    from dagster_io.models import Assertion, Mention, MentionType, Provenance

    if not docs:
        return [], []

    _llm_model = os.environ.get("LLM_MODEL", "unknown")
    bench_model = os.environ.get("CATALYST_BENCH_MODEL") or _llm_model

    _ner_pipeline, spo_pipeline, _llm_client, _embedder = _build_pipelines()

    all_assertions: list[dict] = []
    all_audit_events: list[dict] = []
    completed = 0
    errors = 0
    total_proposition_retries = 0

    logger.info(
        "extract_with_shared_clusters (Phase 3): %d docs, concurrency=%d, code_location=%s, model=%s",
        len(docs),
        max_concurrency,
        code_location,
        bench_model,
    )

    def _run_doc_spo(doc_idx: int, doc: "_Doc") -> tuple[int, dict]:
        clusters = cached_clusters.get(doc.doc_id)
        if clusters is None:
            logger.debug("extract_with_shared_clusters: no clusters for doc_id=%s — skipping", doc.doc_id)
            return doc_idx, {
                "mentions": [],
                "propositions": [],
                "audit_events": [],
                "mention_retries": 0,
                "proposition_retries": 0,
                "status": "skipped",
                "_doc_id": doc.doc_id,
                "_chunk_metadata": doc.chunk_metadata or {},
                "_chunk_id": getattr(doc.chunks[0], "chunk_id", "") if doc.chunks else "",
                "_chunk_text": doc.full_text,
                "_chunk_document_id": doc.doc_id,
            }
        loop = asyncio.new_event_loop()
        try:
            start = time.monotonic()
            result = loop.run_until_complete(
                _process_doc_spo_only(
                    spo_pipeline,
                    doc,
                    clusters,
                    bench_model=bench_model,
                    max_retries=max_retries,
                    doc_mentions=(shared_mentions.get(doc.doc_id) if shared_mentions else None),
                )
            )
            duration = time.monotonic() - start
            LLM_REQUEST_DURATION.labels(model=_llm_model, operation="spo_only").observe(duration)
            LLM_REQUESTS.labels(model=_llm_model, operation="spo_only", status="success").inc()
            return doc_idx, result
        except Exception as e:
            LLM_REQUESTS.labels(model=_llm_model, operation="spo_only", status="error").inc()
            logger.error("SPO-only extraction failed for doc %s: %s", doc.doc_id, e)
            raise
        finally:
            loop.close()

    from concurrent.futures import as_completed

    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {pool.submit(_run_doc_spo, i, doc): i for i, doc in enumerate(docs)}
        for future in as_completed(futures):
            doc_idx, result = future.result()
            chunk_meta = result.get("_chunk_metadata", {})
            chunk_id = result.get("_chunk_id", "")
            chunk_doc_id = result.get("_chunk_document_id", "")

            for a in result["propositions"]:
                a["_chunk_metadata"] = chunk_meta
                a["_chunk_id"] = chunk_id
                a["_chunk_document_id"] = chunk_doc_id

            all_assertions.extend(result["propositions"])
            all_audit_events.extend(result.get("audit_events", []))
            total_proposition_retries += result.get("proposition_retries", 0)

            if result["status"] == "failed":
                errors += 1

            completed += 1

    # Build Assertion domain models
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

        assertion_models.append(
            Assertion(
                subject_text=subj_text,
                subject_mention_id="",
                predicate=a.get("predicate", ""),
                object_text=obj_text,
                object_mention_id="",
                confidence=a.get("confidence", 1.0),
                negated=a.get("negated", False),
                hedged=a.get("hedged", False),
                qualifiers=a.get("qualifiers", {}),
                provenance=a_prov,
            )
        )

    # ── Build Mention domain models from Phase A NER output ───────────────────
    # When shared_mentions is provided (Phase B harness path), convert each
    # raw mention dict to a Mention domain model so callers see >0 mentions.
    # Mirrors the mention-building block in extract_validated (lines ~795-856).
    mention_models: list = []
    if shared_mentions:
        for doc in docs:
            raw_mentions = shared_mentions.get(doc.doc_id) or []
            cm = doc.chunk_metadata or {}
            first_chunk = doc.chunks[0] if doc.chunks else None
            first_chunk_id = getattr(first_chunk, "chunk_id", "") if first_chunk else ""
            start_s = cm.get("start_s")
            end_s = cm.get("end_s")
            speaker = cm.get("speaker")
            for m in raw_mentions:
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

                prov = Provenance(
                    source_document_id=doc.doc_id,
                    chunk_id=first_chunk_id,
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
                        document_id=doc.doc_id,
                        chunk_id=first_chunk_id,
                        text=m.get("text", ""),
                        mention_type=mention_type,
                        span_start=m.get("span_start"),
                        span_end=m.get("span_end"),
                        confidence=m.get("confidence", 1.0),
                        context=m.get("context", ""),
                        provenance=prov,
                    )
                )

    logger.info(
        "extract_with_shared_clusters complete: %d mentions, %d assertions from %d docs (%d failures, %d retries)",
        len(mention_models),
        len(assertion_models),
        len(docs),
        errors,
        total_proposition_retries,
    )

    extract_with_shared_clusters.last_stats = {
        "doc_count": len(docs),
        "mention_count": len(mention_models),
        "assertion_count": len(assertion_models),
        "proposition_retries": total_proposition_retries,
        "errors": errors,
        "audit_events": all_audit_events,
    }

    return mention_models, assertion_models


def extract_validated(
    chunks: list,
    code_location: str,
    *,
    max_concurrency: int = 5,
    max_retries: int = 3,
) -> tuple[list, list]:
    """Run validated extraction on a list of TextChunk objects.

    Phase 2 (CD-j6d3): entity-anchored flow.
    Chunks are grouped by document_id → NER once per doc → cluster_entities →
    pack_evidence → SPO fan-out per evidence window.

    Concurrency is at the doc level (unit of work = one full doc end-to-end).
    Within a doc, SPO windows run sequentially (the cluster→pack→fan-out chain
    shares state).

    Domain-specific prompts are loaded automatically from PROMPT_REGISTRY_DIR.

    Args:
        chunks: List of TextChunk objects (must have .text, .document_id, .chunk_id).
        code_location: For metrics labeling.
        max_concurrency: Max parallel doc-level extraction tasks.
        max_retries: Max repair attempts per stage per evidence window.

    Returns:
        (all_mentions, all_assertions) — flattened lists of Mention and
        Assertion domain model instances.
    """
    from dagster_io.models import Assertion, Mention, MentionType, Provenance

    if not chunks:
        return [], []

    _llm_model = os.environ.get("LLM_MODEL", "unknown")
    bench_model = os.environ.get("CATALYST_BENCH_MODEL") or _llm_model

    ner_pipeline, spo_pipeline, llm_client, _embedder = _build_pipelines()
    # Encoder/specialist models produce deterministic output — skip retries.
    _is_encoder = getattr(llm_client, "structured_method", "") in ("gliner", "nuextract", "universalner")
    _max_retries = 0 if _is_encoder else max_retries

    # Group chunks → docs
    docs = _group_chunks_into_docs(chunks)

    all_mentions: list[dict] = []
    all_assertions: list[dict] = []
    all_audit_events: list[dict] = []
    completed = 0
    errors = 0
    total_mention_retries = 0
    total_proposition_retries = 0

    logger.info(
        "Starting validated extraction (Phase 2 entity-anchored): %d chunks → %d docs, "
        "concurrency=%d, code_location=%s",
        len(chunks),
        len(docs),
        max_concurrency,
        code_location,
    )

    def _run_doc(doc_idx: int, doc: "_Doc") -> tuple[int, dict]:
        """Run one doc end-to-end through the entity-anchored pipeline (sync wrapper)."""
        loop = asyncio.new_event_loop()
        try:
            start = time.monotonic()
            result = loop.run_until_complete(
                _process_doc(
                    ner_pipeline,
                    spo_pipeline,
                    doc,
                    bench_model=bench_model,
                    max_retries=_max_retries,
                )
            )
            duration = time.monotonic() - start
            LLM_REQUEST_DURATION.labels(model=_llm_model, operation="validated_extraction").observe(duration)
            LLM_REQUESTS.labels(model=_llm_model, operation="validated_extraction", status="success").inc()
            return doc_idx, result
        except Exception as e:
            LLM_REQUESTS.labels(model=_llm_model, operation="validated_extraction", status="error").inc()
            logger.error("Extraction failed for doc %s: %s", doc.doc_id, e)
            raise
        finally:
            loop.close()

    from concurrent.futures import as_completed

    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {pool.submit(_run_doc, i, doc): i for i, doc in enumerate(docs)}
        for future in as_completed(futures):
            doc_idx, result = future.result()  # raises on permanent failure
            chunk_meta = result.get("_chunk_metadata", {})
            chunk_id = result.get("_chunk_id", "")
            chunk_text = result.get("_chunk_text", "")
            chunk_doc_id = result.get("_chunk_document_id", "")

            # Tag each mention/assertion with its source doc/chunk metadata
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
            total_mention_retries += result.get("mention_retries", 0)
            total_proposition_retries += result.get("proposition_retries", 0)

            if result["status"] == "failed":
                errors += 1

            completed += 1
            if completed % 50 == 0 or completed == len(docs):
                logger.info(
                    "Validated extraction progress: %d/%d docs (%.0f%%)%s",
                    completed,
                    len(docs),
                    completed / len(docs) * 100,
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
        "Validated extraction complete: %d mentions, %d assertions from %d docs (%d chunks) "
        "(%d mention retries, %d proposition retries, %d failures)",
        len(mention_models),
        len(assertion_models),
        len(docs),
        len(chunks),
        total_mention_retries,
        total_proposition_retries,
        errors,
    )

    # Build pipeline breakdown from audit events
    pipeline_breakdown = _build_pipeline_breakdown(all_audit_events)

    # Stash stats for callers that need them (e.g. benchmark tests).
    # Does not change the return signature — production assets are unaffected.
    _context_window = int(os.environ.get("LLM_CONTEXT_WINDOW", "4096"))

    # Total LLM calls across the run. Validators are deterministic (no LLM),
    # so we count only the extract/repair nodes.  Two-tier:
    #   1. Audit events (exact, when audit logging is on)
    #   2. Derived from docs + retries (when audit isn't captured)
    # Phase 2: base is 1 NER per doc + N SPO (one per evidence window).
    _LLM_NODES = {
        "extract_ner",
        "repair_ner",
        "extract_spo",
        "repair_spo",
        # Legacy node names kept for backward-compat with old audit events
        "mention_extractor",
        "repair_mention_extractor",
        "proposition_extractor",
        "repair_proposition_extractor",
    }
    audit_call_count = sum(1 for e in all_audit_events if e.get("node_name") in _LLM_NODES)
    derived_call_count = (
        completed  # 1 NER per doc minimum
        + total_mention_retries
        + total_proposition_retries
    )
    llm_call_count = audit_call_count or derived_call_count

    extract_validated.last_stats = {
        "chunk_count": len(chunks),
        "doc_count": len(docs),
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
