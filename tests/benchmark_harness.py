"""Unified benchmark harness -- single entry point for extraction benchmarking.

Wraps the existing test infrastructure into a clean CLI that:
1. Runs extraction across all configured models
2. Computes F1/precision/recall against ground truth (if available)
3. Generates the benchmark report JSON for the viewer SPA
4. Streams every harness/exgraph/langgraph/dagster event into one
   ``events.jsonl`` per run (consumed live by the viewer's LiveGantt)
5. Reports latency, throughput, hallucination rate, quality/speed ratio

Usage:
    # Full benchmark:
    python tests/benchmark_harness.py --regen --timeout 600

    # Quick run (cached fixtures):
    python tests/benchmark_harness.py

    # Generate ground truth from best model:
    python tests/benchmark_harness.py --generate-ground-truth --ground-truth-model gpt-4o
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dagster_io import event_tail
from tests._bench_tui import BenchLiveUI, DequeHandler
from tests.benchmark_config import ALL_MODELS, LOCAL_MODELS, BenchmarkConfig, ModelConfig

# ── Default encoder/SPO lists — resolved from benchmark_config tags ──────────


def _default_ensemble() -> list[str]:
    """Encoders that vote in Phase 1 NER consensus (encoder or extraction-specialist tags)."""
    return [m.name for m in ALL_MODELS if "encoder" in m.tags or "extraction-specialist" in m.tags]


def _default_spo() -> list[str]:
    """Models that run Phase 4 SPO (tier1, tier2, or cloud tags)."""
    return [m.name for m in ALL_MODELS if any(t in m.tags for t in ("tier1", "tier2", "cloud"))]


from tests.shared.extraction_scoring import (
    compute_model_scores,
    print_benchmark_report,
)
from tests.shared.ground_truth import generate_ensemble_ground_truth
from tests.shared.medallion import load_chunks
from tests.shared.report import build_report_json
from tests.shared.store import BenchmarkStore


def _ansi_palette(use_color: bool) -> dict[str, str]:
    """ANSI escape palette gated on TTY presence — synthwave-leaning.

    Keys are 1-2 char so call-site f-strings stay readable. Returns empty
    strings when ``use_color`` is false, so log files / piped output stay
    plain ASCII.
    """
    if not use_color:
        return {k: "" for k in ("x", "k", "m", "c", "y", "g", "e", "r")}
    return {
        "x": "\033[0m",  # reset
        "k": "\033[38;5;245m",  # zinc-ish neutral
        "m": "\033[38;5;201m",  # synthwave magenta
        "c": "\033[38;5;51m",  # neon cyan
        "y": "\033[38;5;227m",  # CRT yellow
        "g": "\033[38;5;46m",  # signal green
        "e": "\033[38;5;253m",  # bright eggshell (default value text)
        "r": "\033[38;5;196m",  # alert red
    }


def _phase_a_build_cluster_cache(
    sample_n: int | None,
    ner_ref_model: str | None = None,
    store: BenchmarkStore | None = None,
    ensemble_models: list[str] | None = None,
    quorum: int | None = None,
) -> tuple[list, dict, dict, dict]:
    """Phase A: NER ensemble + cluster once per doc.

    v4 (CD-z6xe): Runs the ensemble pipeline (``build_ensemble_pipeline``) on
    each document, using all encoder-tagged models from ENCODER_MODELS +
    EXTRACTION_MODELS as the ensemble panel.  For each doc the function:

    1. Runs ``build_ensemble_pipeline`` to get per_encoder_mentions,
       consensus_mentions, entity_clusters, evidence_windows, and
       rejected_mentions.
    2. Saves a per-encoder extraction fixture for each encoder so ``_run_model``
       can return it directly without rerunning NER.
    3. Saves an ``extraction_ensemble`` fixture with the consensus mentions.
    4. Stores the full ``CachedNerResult`` (including v4 fields) in the
       ClusterCache so warm cache hits return everything.

    Returns ``(docs, cluster_by_doc, mentions_by_doc, per_encoder_by_doc)`` where:
      - ``cluster_by_doc``: ``{doc_id → list[EntityCluster]}``
      - ``mentions_by_doc``: ``{doc_id → list[consensus_mention_dict]}``
        (the consensus set that flows into SPO extraction)
      - ``per_encoder_by_doc``: ``{encoder_name → {doc_id → list[mention_dict]}}``
        (raw per-encoder mentions for observability / fixture saving)

    Backwards-compat note: the old ``(docs, cluster_by_doc, mentions_by_doc)``
    3-tuple callers are broken by this change intentionally — only
    ``_phase_a_build_cluster_cache`` is called from ``main()`` in this file.
    """

    from dagster_io.cluster_cache import ClusterCache
    from dagster_io.extraction import _group_chunks_into_docs

    # Resolve the encoder panel from benchmark_config — encoder-tagged models
    # only.  extraction-specialist models that are NOT encoder-tagged use Ollama
    # and run as LLM-tier in Phase B, not as part of the ensemble.
    from tests.benchmark_config import ENCODER_MODELS, get_model_by_name

    if ensemble_models is not None:
        # Explicit list from --ensemble flag; resolve each name to a ModelConfig
        encoder_cfgs = []
        for name in ensemble_models:
            cfg = get_model_by_name(name)
            if cfg:
                encoder_cfgs.append(cfg)
            else:
                print(f"  WARNING: ensemble model '{name}' not found in benchmark_config.py — skipping")
    else:
        encoder_cfgs = [m for m in ENCODER_MODELS if "encoder" in m.tags]

    # Fall back to single-encoder "ensemble" (gliner-large) when the list is
    # empty so the harness still produces something useful.
    if not encoder_cfgs:
        fallback_name = ner_ref_model or os.environ.get("BENCH_NER_REF_MODEL", "gliner-large")
        fallback_cfg = get_model_by_name(fallback_name)
        if fallback_cfg:
            encoder_cfgs = [fallback_cfg]

    try:
        medallion_chunks = load_chunks(sample_per_domain=sample_n)
        if not medallion_chunks:
            return [], {}, {}, {}

        from dagster_io import TextChunk

        eval_chunks = [TextChunk(**c) for c in medallion_chunks]
        docs = _group_chunks_into_docs(eval_chunks)

        # Build ensemble pipeline
        ensemble_pipeline = _build_ensemble_pipeline_for_phase_a(encoder_cfgs, quorum=quorum)

        cluster_cache = ClusterCache()
        params: dict = {}
        cluster_by_doc: dict[str, list] = {}
        mentions_by_doc: dict[str, list] = {}
        # per_encoder_by_doc[encoder_name][doc_id] = list[mention_dict]
        per_encoder_by_doc: dict[str, dict[str, list]] = {m.name: {} for m in encoder_cfgs}

        # Wall-clock for the entire Phase A loop (encoders run in parallel so
        # wall time < sum of per-encoder times).
        phase_a_t0 = time.monotonic()

        for doc in docs:
            # Check warm cache first (skips ensemble run entirely on re-runs)
            cache_key_model = "ensemble_v4"
            cached = cluster_cache.get(
                doc_id=doc.doc_id,
                doc_text=doc.full_text,
                ner_model=cache_key_model,
                params=params,
            )
            if cached is not None and cached.per_encoder_mentions:
                # Warm hit — already has v4 fields
                cluster_by_doc[doc.doc_id] = cached.clusters
                mentions_by_doc[doc.doc_id] = cached.mentions
                for enc_name, enc_mentions in cached.per_encoder_mentions.items():
                    per_encoder_by_doc.setdefault(enc_name, {})[doc.doc_id] = enc_mentions
                continue

            # Cold miss — run the ensemble pipeline
            ner_result = _run_ensemble_for_doc(ensemble_pipeline, doc, encoder_cfgs)

            cluster_by_doc[doc.doc_id] = ner_result.clusters
            mentions_by_doc[doc.doc_id] = ner_result.mentions

            for enc_name, enc_mentions in ner_result.per_encoder_mentions.items():
                per_encoder_by_doc.setdefault(enc_name, {})[doc.doc_id] = enc_mentions

            # Store v4 result in the cluster cache
            cluster_cache.put(
                doc_id=doc.doc_id,
                doc_text=doc.full_text,
                ner_model=cache_key_model,
                params=params,
                result=ner_result,
            )

        # Phase A wall-clock (all encoders ran in parallel, so this is < sum).
        phase_a_duration_s = time.monotonic() - phase_a_t0

        # ── Save per-encoder fixtures so _run_model can return them directly ──
        if store is not None:
            # Read back ner_encoder_completed events from the JSONL to get
            # real per-encoder duration_s and mention_count.
            enc_event_stats = _read_encoder_event_stats()

            total_doc_chars = sum(len(d.full_text) for d in docs)
            n_docs = len(docs)

            for enc_cfg in encoder_cfgs:
                enc_name = enc_cfg.name
                enc_docs_mentions = per_encoder_by_doc.get(enc_name, {})
                all_enc_mentions = []
                for _doc_id, enc_mentions in enc_docs_mentions.items():
                    all_enc_mentions.extend(enc_mentions)

                # Per-encoder timing from audit events (sum across all docs for
                # this encoder — each doc emits one ner_encoder_completed event).
                ev_stat = enc_event_stats.get(enc_name, {})
                enc_duration_s = ev_stat.get("duration_s", 0.0)
                enc_tok_per_s = (total_doc_chars / 4.0 / enc_duration_s) if enc_duration_s > 0 else 0.0

                enc_fixture = {
                    "model": enc_cfg.model,
                    "base_url": enc_cfg.base_url or "",
                    "structured_method": enc_cfg.structured_method,
                    "mentions": all_enc_mentions,
                    "assertions": [],
                    "stats": {
                        "chunk_count": n_docs,
                        "duration_s": enc_duration_s,
                        "tokens_per_sec": enc_tok_per_s,
                        "mention_count": len(all_enc_mentions),
                        "assertion_count": 0,
                        "llm_call_count": n_docs,
                        "mention_retries": 0,
                        "proposition_retries": 0,
                        "errors": ev_stat.get("error_count", 0),
                        "phase": "a_encoder",
                    },
                }
                store.save_fixture(f"extraction_{enc_cfg.model}", enc_fixture)

            # ── Save ensemble fixture ──────────────────────────────────────────
            all_consensus_mentions = []
            for doc_mentions in mentions_by_doc.values():
                all_consensus_mentions.extend(doc_mentions)
            ensemble_tok_per_s = (total_doc_chars / 4.0 / phase_a_duration_s) if phase_a_duration_s > 0 else 0.0
            ensemble_fixture = {
                "model": "ensemble",
                "base_url": "",
                "structured_method": "ensemble",
                "mentions": all_consensus_mentions,
                "assertions": [],
                "stats": {
                    "chunk_count": n_docs,
                    "duration_s": phase_a_duration_s,
                    "tokens_per_sec": ensemble_tok_per_s,
                    "mention_count": len(all_consensus_mentions),
                    "assertion_count": 0,
                    "llm_call_count": n_docs,
                    "mention_retries": 0,
                    "proposition_retries": 0,
                    "errors": 0,
                    "phase": "a_ensemble",
                    "n_encoders": len(encoder_cfgs),
                },
            }
            store.save_fixture("extraction_ensemble", ensemble_fixture)

        return docs, cluster_by_doc, mentions_by_doc, per_encoder_by_doc

    except Exception:
        # Propagate — Phase A failure is not silently swallowed
        raise


def _read_encoder_event_stats() -> dict[str, dict]:
    """Read ``ner_encoder_completed`` events from the current run's JSONL.

    Returns a dict keyed by encoder name with aggregated stats:
        {encoder_name: {"duration_s": float, "mention_count": int, "error_count": int}}

    Falls back to empty dict when event_tail is not configured or the file
    doesn't exist yet (e.g. warm-cache-only runs).
    """
    import json as _json

    from dagster_io import event_tail as _et

    result: dict[str, dict] = {}
    if not _et.is_configured() or _et._path is None:
        return result
    path = _et._path
    if not path.exists():
        return result

    try:
        lines = path.read_text().splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if ev.get("node_name") != "ner_encoder_completed":
                continue
            enc_name = ev.get("model") or (ev.get("details") or {}).get("encoder") or ""
            if not enc_name:
                continue
            details = ev.get("details") or {}
            duration = float(details.get("duration_s") or 0.0)
            is_error = ev.get("status") == "error"

            if enc_name not in result:
                result[enc_name] = {"duration_s": 0.0, "mention_count": 0, "error_count": 0}
            result[enc_name]["duration_s"] += duration
            if not is_error:
                result[enc_name]["mention_count"] += int(details.get("mention_count") or 0)
            else:
                result[enc_name]["error_count"] += 1
    except Exception:
        pass  # Best-effort — never fail fixture saving over stats reading

    return result


def _build_ensemble_pipeline_for_phase_a(encoder_cfgs: list, quorum: int | None = None):
    """Build a ``build_ensemble_pipeline`` instance for the Phase A encoder panel.

    Resolves one ExtractionClient per encoder using ``_resolve_client`` and
    wraps each in a ``ner_stage_config`` with ``model_override=encoder_name``
    and ``max_retries=0`` (encoders are deterministic).

    ``quorum`` overrides the default ``ceil(N/2)`` in ConsensusNode when set.
    """
    from catalyst_exgraph.config import ner_stage_config
    from catalyst_exgraph.pipeline import build_ensemble_pipeline
    from catalyst_exgraph.resource import _build_mcp_client, _resolve_client

    mcp_client = _build_mcp_client()
    encoder_stage_cfgs = []
    clients: dict = {}

    for enc_cfg in encoder_cfgs:
        # Each encoder gets a StageConfig with model_override = bench model name
        # so NerEnsembleNode keys per_encoder_mentions by the bench name.
        stage_cfg = ner_stage_config(model=enc_cfg.model, max_retries=0)
        # Rebuild with model_override=enc_cfg.name for ensemble keying
        from catalyst_exgraph.config import StageConfig

        stage_cfg = StageConfig(**{**stage_cfg.__dict__, "model_override": enc_cfg.name})
        encoder_stage_cfgs.append(stage_cfg)
        client = _resolve_client(enc_cfg.model, base_url=enc_cfg.base_url or None, api_key=None)
        clients[enc_cfg.name] = client

    return build_ensemble_pipeline(
        encoders=encoder_stage_cfgs,
        clients=clients,
        mcp_client=mcp_client,
        embedder=None,  # proximity-only clustering in Phase A
        quorum=quorum,
    )


def _run_ensemble_for_doc(ensemble_pipeline, doc, encoder_cfgs: list):
    """Synchronous wrapper: run ensemble pipeline for one doc, return CachedNerResult."""
    import asyncio as _asyncio

    from dagster_io.cluster_cache import CachedNerResult

    loop = _asyncio.new_event_loop()
    try:
        doc_ner_chunk_id = f"{doc.doc_id}:_doc_ensemble"
        state_input = {
            "raw_text": doc.full_text,
            "doc_id": doc.doc_id,
            "chunk_id": doc_ner_chunk_id,
            "model": "ensemble",
            "source_metadata": {
                "document_id": doc.doc_id,
                "chunk_id": doc_ner_chunk_id,
                "chunk_index": 0,
                "total_chunks": len(doc.chunks),
                "chunk_metadata": doc.chunk_metadata or {},
                "domain": (doc.chunk_metadata or {}).get("domain"),
                "speaker_label": (doc.chunk_metadata or {}).get("speaker"),
                "temporal_start_ms": None,
                "temporal_end_ms": None,
            },
            "max_retries": 0,
        }
        result = loop.run_until_complete(ensemble_pipeline.ainvoke(state_input))

        clusters = result.get("entity_clusters") or []
        # Consensus mentions are the canonical set for SPO
        consensus_mentions = result.get("consensus_mentions") or []
        per_encoder_mentions: dict[str, list] = result.get("per_encoder_mentions") or {}
        evidence_windows: list = result.get("evidence_windows") or []
        rejected_mentions: list = result.get("rejected_mentions") or []

        return CachedNerResult(
            clusters=clusters,
            mentions=consensus_mentions,
            per_encoder_mentions=per_encoder_mentions,
            evidence_windows=evidence_windows,
            rejected_mentions=rejected_mentions,
        )
    finally:
        loop.close()


def _run_ner_and_cluster(ner_pipeline, doc, bench_model: str):
    """Synchronous wrapper: run NER pipeline and return a CachedNerResult for a doc.

    Legacy single-NER path — still used when Phase A falls back to a single
    encoder (e.g. ENCODER_MODELS is empty and only a ref_model is available).
    Not called on the normal v4 ensemble path.
    """
    import asyncio as _asyncio

    from dagster_io.cluster_cache import CachedNerResult

    loop = _asyncio.new_event_loop()
    try:
        # Phase A is doc-scoped — NER runs over the full concatenated doc.
        # Use a stable synthetic chunk_id (`{doc_id}:_doc_ner`) so events emit
        # with a non-empty chunk_id; otherwise the StateInspector rail rejects
        # them and the inspector looks empty during Phase A.
        doc_ner_chunk_id = f"{doc.doc_id}:_doc_ner"
        ner_state_input = {
            "raw_text": doc.full_text,
            "doc_id": doc.doc_id,
            "chunk_id": doc_ner_chunk_id,
            "model": bench_model,
            "source_metadata": {
                "document_id": doc.doc_id,
                "chunk_id": doc_ner_chunk_id,
                "chunk_index": 0,
                "total_chunks": len(doc.chunks),
                "chunk_metadata": doc.chunk_metadata or {},
                "domain": (doc.chunk_metadata or {}).get("domain"),
                "speaker_label": (doc.chunk_metadata or {}).get("speaker"),
                "temporal_start_ms": None,
                "temporal_end_ms": None,
            },
            "max_retries": 0,
        }
        ner_result = loop.run_until_complete(ner_pipeline.ainvoke(ner_state_input))
        clusters = ner_result.get("entity_clusters") or []
        mentions = (ner_result.get("stages") or {}).get("ner", {}).get("accepted") or []
        return CachedNerResult(clusters=clusters, mentions=mentions)
    finally:
        loop.close()


def _run_model(
    cfg: ModelConfig,
    timeout: int,
    store: BenchmarkStore,
    run_id: str,
    all_videos: bool = False,  # retained for back-compat; in-process path runs across all domains
    shared_docs: list | None = None,
    shared_clusters: dict | None = None,
    shared_mentions: dict | None = None,
    phase_a_encoder_names: set[str] | None = None,
) -> dict | None:
    """Run extraction for one model in-process.

    v4 routing (CD-z6xe):
    - **Encoder-tier** (``"encoder"`` in cfg.tags or model name is in the
      Phase A encoder panel ``phase_a_encoder_names``): the per-encoder fixture
      was already saved during Phase A.  Read it back from the store and return
      immediately — no SPO work needed.
    - **Synthetic ``ensemble`` model**: read ``extraction_ensemble`` fixture
      saved by Phase A and return it directly.
    - **LLM-tier** (everything else): run Phase B SPO via
      ``extract_with_shared_clusters`` over the cached evidence_windows +
      consensus_mentions.  Same path as v3, just renamed semantically.

    If an encoder-tier model is NOT in the Phase A ensemble (i.e. it was
    requested via --models but isn't one of the v4 encoder_cfgs), the legacy
    ``extract_validated`` path is used so the user can still bench an encoder
    independently of the consensus set.

    ``all_videos`` is retained as a no-op for CLI compatibility — the
    in-process path is naturally cross-domain.  Use ``BENCH_SAMPLE_PER_DOMAIN``
    to cap volume.
    """
    from dagster_io import TextChunk
    from dagster_io.extraction import extract_validated
    from tests.shared.medallion import load_chunks

    # ── Encoder-tier shortcut: return the pre-saved Phase A fixture ──────────
    # For any model with "encoder" in its tags that participated in Phase A,
    # _phase_a_build_cluster_cache already saved the per-encoder fixture.
    # Return it immediately (no SPO, no LLM calls).
    _is_encoder_tier = "encoder" in cfg.tags or "extraction-specialist" in cfg.tags
    _in_phase_a_panel = phase_a_encoder_names is not None and cfg.name in phase_a_encoder_names

    if _is_encoder_tier and _in_phase_a_panel:
        fixture = store.load_fixture(f"extraction_{cfg.model}")
        if fixture is not None:
            run = store.load_run(run_id)
            if run is not None:
                run.save_extraction(cfg.model, fixture)
            store.save_extraction(cfg.model, fixture)
            return fixture
        # Pre-saved fixture missing — fall through to the legacy path below so
        # we still produce output rather than silently returning None.

    # ── Synthetic "ensemble" model: return the ensemble fixture from Phase A ─
    if cfg.name == "ensemble" or cfg.model == "ensemble":
        fixture = store.load_fixture("extraction_ensemble")
        if fixture is not None:
            run = store.load_run(run_id)
            if run is not None:
                run.save_extraction("ensemble", fixture)
            store.save_extraction("ensemble", fixture)
            return fixture
        event_tail.append(
            source="harness",
            node_name="model_run",
            status="error",
            model=cfg.name,
            details={"reason": "ensemble_fixture_missing", "hint": "run Phase A first"},
        )
        return None

    is_cloud = "cloud" in cfg.tags
    if is_cloud:
        api_key = cfg.api_key or os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        base_url = cfg.base_url
    else:
        api_key = "unused"
        base_url = cfg.base_url or "http://localhost:11434/v1"

    # Per-model env. We mutate os.environ for the duration of the
    # extract_validated call (extract_validated reads LLM_MODEL etc. at
    # call time) and restore it after so the next model starts clean.
    overrides = {
        "LLM_MODEL": cfg.model,
        "LLM_BASE_URL": base_url,
        "LLM_API_KEY": api_key,
        "OPENAI_API_KEY": api_key,
        "LLM_STRUCTURED_METHOD": cfg.structured_method,
        "LLM_MAX_TOKENS": str(cfg.max_tokens),
        "LLM_CONTEXT_WINDOW": str(cfg.context_window),
        "LLM_TIMEOUT": str(timeout),
        "PROMPT_REGISTRY_DIR": str(ROOT / "k8s" / "shared" / "prompts"),
        "CATALYST_BENCH_MODEL": cfg.name,
    }
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)

    try:
        # Stratified sample so cross-domain runs don't blow up on
        # open-leaks's 3.6M-chunk corpus.
        sample_n_raw = os.environ.get("BENCH_SAMPLE_PER_DOMAIN", "50")
        sample_n: int | None = int(sample_n_raw)
        if sample_n == 0:
            sample_n = None

        start = time.monotonic()

        if shared_clusters is not None and shared_docs is not None:
            # ── LLM-tier Phase B: SPO fan-out over cached consensus mentions ──
            # NER already done in Phase A via ensemble; only run SPO.
            from dagster_io.extraction import extract_with_shared_clusters

            if not shared_docs:
                event_tail.append(
                    source="harness",
                    node_name="model_run",
                    status="error",
                    model=cfg.name,
                    details={"reason": "no_docs_in_phase_a", "hint": "run Phase A first"},
                )
                return None

            cap = f"{sample_n}/domain (phase-b)" if sample_n is not None else "full (phase-b)"
            print(f"\n  [{cfg.name}] {len(shared_docs)} docs from Phase A (cap={cap})", flush=True)

            try:
                mentions, assertions = extract_with_shared_clusters(
                    shared_docs,
                    shared_clusters,
                    shared_mentions=shared_mentions,
                    code_location="media_ingest",
                    max_concurrency=1,
                )
            except Exception as exc:
                event_tail.append(
                    source="harness",
                    node_name="model_run",
                    status="error",
                    model=cfg.name,
                    details={"reason": type(exc).__name__, "message": str(exc)[:500]},
                )
                return None
            pipeline_stats = getattr(extract_with_shared_clusters, "last_stats", {})
            eval_chunk_count = sum(len(d.chunks) for d in shared_docs)
        else:
            # ── Legacy path: full NER + SPO in one shot ──────────────────────────
            # Used for: encoder-tier models NOT in Phase A panel, and any fallback.
            medallion_chunks = load_chunks(sample_per_domain=sample_n)
            if not medallion_chunks:
                event_tail.append(
                    source="harness",
                    node_name="model_run",
                    status="error",
                    model=cfg.name,
                    details={"reason": "no_chunks_in_medallion", "hint": "run task seed first"},
                )
                return None

            eval_chunks = [TextChunk(**c) for c in medallion_chunks]
            cap = f"{sample_n}/domain" if sample_n is not None else "full"
            print(f"\n  [{cfg.name}] {len(eval_chunks)} chunks (cap={cap})", flush=True)

            try:
                mentions, assertions = extract_validated(
                    eval_chunks,
                    code_location="media_ingest",
                    max_concurrency=1,
                )
            except Exception as exc:
                event_tail.append(
                    source="harness",
                    node_name="model_run",
                    status="error",
                    model=cfg.name,
                    details={"reason": type(exc).__name__, "message": str(exc)[:500]},
                )
                return None
            pipeline_stats = getattr(extract_validated, "last_stats", {})
            eval_chunk_count = len(eval_chunks)

        duration = time.monotonic() - start
        # eval_chunk_count is set by both branches above; compute stats from it
        total_input_chars = eval_chunk_count * 1000  # approximate when no eval_chunks list
        if shared_clusters is None:
            # Have the real eval_chunks list from legacy path
            total_input_chars = sum(len(c.text) for c in eval_chunks)
        est_input_tokens = total_input_chars // 4
        est_output_tokens = (len(mentions) + len(assertions)) * 50
        est_total_tokens = est_input_tokens + est_output_tokens
        tokens_per_sec = est_total_tokens / duration if duration > 0 else 0

        fixture = {
            "model": cfg.model,
            "base_url": base_url,
            "structured_method": cfg.structured_method,
            "mentions": [m.model_dump(mode="json") for m in mentions],
            "assertions": [a.model_dump(mode="json") for a in assertions],
            "stats": {
                "chunk_count": eval_chunk_count,
                "duration_s": round(duration, 1),
                "total_input_chars": total_input_chars,
                "est_total_tokens": est_total_tokens,
                "tokens_per_sec": round(tokens_per_sec, 1),
                "mention_count": len(mentions),
                "assertion_count": len(assertions),
                "mention_retries": pipeline_stats.get("mention_retries", 0),
                "proposition_retries": pipeline_stats.get("proposition_retries", 0),
                "errors": pipeline_stats.get("errors", 0),
                "llm_call_count": pipeline_stats.get("llm_call_count", 0),
                "pipeline": pipeline_stats.get("pipeline", {}),
                "audit_events": pipeline_stats.get("audit_events", []) if os.environ.get("SAVE_AUDIT_LOG") else [],
            },
        }

        # Save into the run namespace so report/score read from one place.
        run = store.load_run(run_id)
        if run is not None:
            run.save_extraction(cfg.model, fixture)
        # Also keep top-level extraction cache for ground-truth ensemble
        # consensus, which reads via store.load_extraction(model).
        store.save_extraction(cfg.model, fixture)
        return fixture
    finally:
        # Restore env so the next model run isn't poisoned.
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load_candidate_chunk_ids(path: Path | None) -> list[str] | None:
    """Read .test-output/gt-candidates.json (or the path given) and return its
    ``chunk_id`` list. Returns None when the file is absent — caller treats
    None as 'no candidate filter, run over the full pool'.
    """
    if path is None:
        return None
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return [c["chunk_id"] for c in payload.get("candidates", []) if c.get("chunk_id")]


def _run_ensemble_gt(
    store: BenchmarkStore,
    ner_models: list[str] | None = None,
    spo_models: list[str] | None = None,
    candidates: list[str] | None = None,
):
    """Generate ensemble ground truth from cached extraction artifacts."""
    print(f"\n{'=' * 70}")
    print("  Ensemble Ground Truth Generation")
    if candidates is not None:
        print(f"  Restricted to {len(candidates)} candidate chunks (sampler seed)")
    print(f"{'=' * 70}")

    existing = store.load_ground_truth("active")
    if existing and existing.get("manually_reviewed"):
        print("\n  Skipped: ground truth has been manually reviewed.")
        print(f"{'=' * 70}")
        return

    ground_truth = generate_ensemble_ground_truth(
        store, ner_models=ner_models, spo_models=spo_models, candidates=candidates
    )
    if ground_truth is None:
        print("\n  Failed: not enough model fixtures for ensemble (need >= 2).")
        print(f"{'=' * 70}")
        return

    # Save with a descriptive name as well as active
    config = ground_truth["ensemble_config"]
    n_models = len(set(config["ner_models"] + config["spo_models"]))
    store.save_ground_truth(f"ensemble-{n_models}model", ground_truth)
    store.save_ground_truth("active", ground_truth)

    print("\n  Ensemble ground truth saved:")
    print(f"    NER models ({len(config['ner_models'])}): {', '.join(config['ner_models'])}")
    print(f"    SPO models ({len(config['spo_models'])}): {', '.join(config['spo_models'])}")
    print(f"    Threshold: NER >= {config['ner_threshold']}, SPO >= {config['spo_threshold']}")
    print(f"    Mentions: {ground_truth['total_mentions']} | Propositions: {ground_truth['total_propositions']}")
    print(f"{'=' * 70}")


def _interactive_prompt() -> argparse.Namespace:
    """Interactive mode -- ask user what to run when no flags provided."""
    print(f"\n{'=' * 70}")
    print("  Extraction Benchmark Harness -- Interactive Mode")
    print(f"{'=' * 70}\n")

    options = [
        ("full", "Full methodology (run models -> ensemble GT -> score -> report)"),
        ("regen", "Regenerate all extraction fixtures"),
        ("ensemble-gt", "Generate ensemble ground truth only"),
        ("local-only", "Skip cloud models (no API key needed)"),
    ]

    for i, (_, desc) in enumerate(options, 1):
        print(f"  [{i}] {desc}")
    print("  [Enter] Default run (use cached fixtures, score, report)")
    print()

    raw = input("  Select options (comma-separated, e.g. 1,4): ").strip()

    args = argparse.Namespace(
        regen=False,
        timeout=300,
        local_only=False,
        generate_ground_truth=False,
        ground_truth_model="gpt-4o",
        generate_gt=False,
        gt_model="gpt-4o",
        ensemble_gt=False,
        full=False,
        ensemble=None,
        spo_models=None,
        ensemble_quorum=None,
        ensemble_only=False,
        spo_only=False,
        no_consensus=False,
        run_id=None,
        label=None,
        list_gt=False,
        list_runs=False,
        use_gt=None,
        clean=False,
        view=False,
        score=False,
        report=False,
        chunk_size=None,
    )

    if raw:
        for choice in raw.split(","):
            choice = choice.strip()
            if choice == "1":
                args.full = True
                args.ensemble_gt = True
            elif choice == "2":
                args.regen = True
            elif choice == "3":
                args.ensemble_gt = True
            elif choice == "4":
                args.local_only = True

    timeout_raw = input(f"  Timeout per model [{args.timeout}s]: ").strip()
    if timeout_raw.isdigit():
        args.timeout = int(timeout_raw)

    print()
    return args


def _save_incremental_report(results: list[dict], store: BenchmarkStore) -> None:
    """Save the benchmark report after each model so cancelled runs keep partial results."""
    try:
        gt = store.load_ground_truth("active")
        chunks_data = load_chunks()
        chunk_texts = {c["chunk_id"]: c["text"] for c in chunks_data} if chunks_data else None
        report = build_report_json(results, ground_truth=gt, chunk_texts=chunk_texts, store=store)
        store.save_top_level_report(report)
    except Exception:
        pass  # best effort


def _list_ground_truths(store: BenchmarkStore) -> None:
    """List available ground truth files."""
    gts = store.list_ground_truths()
    if not gts:
        print("  No ground truth files found.")
        print(f"  Location: {store.ground_truth_uri}")
        return
    print(f"\n  Available ground truths ({store.ground_truth_uri}):")
    for name in gts:
        gt = store.load_ground_truth(name)
        if gt:
            reviewed = "reviewed" if gt.get("manually_reviewed") else "unreviewed"
            ref = gt.get("reference_model", "?")
            mentions = gt.get("total_mentions", "?")
            props = gt.get("total_propositions", "?")
            active = " (ACTIVE)" if name == "active" else ""
            print(f"    {name}{active}: {ref} | {mentions} mentions, {props} propositions | {reviewed}")
        else:
            print(f"    {name}")


def _list_models() -> None:
    """List every model configured in tests/benchmark_config.py.

    Names are what ``--ensemble`` / ``--spo-models`` expect (comma-separated).
    Grouped by tier so it's obvious which are encoder vs LLM vs cloud.
    """
    rows: list[tuple[str, str, str, str]] = []
    for m in ALL_MODELS:
        tags = ",".join(m.tags)
        if "encoder" in m.tags:
            tier = "encoder"
        elif "extraction-specialist" in m.tags:
            tier = "specialist"
        elif "cloud" in m.tags:
            tier = "cloud"
        elif "tier1" in m.tags:
            tier = "tier1"
        elif "tier2" in m.tags:
            tier = "tier2"
        else:
            tier = "—"
        rows.append((tier, m.name, m.model, tags))

    tier_order = {"encoder": 0, "specialist": 1, "tier1": 2, "tier2": 3, "cloud": 4, "—": 9}
    rows.sort(key=lambda r: (tier_order.get(r[0], 99), r[1]))

    print(
        f"\n  {len(rows)} models configured ({len(LOCAL_MODELS)} local, {len(ALL_MODELS) - len(LOCAL_MODELS)} cloud):\n"
    )
    print(f"    {'tier':<10} {'name':<24} {'model':<32} tags")
    print(f"    {'-' * 10} {'-' * 24} {'-' * 32} {'-' * 30}")
    for tier, name, model, tags in rows:
        print(f"    {tier:<10} {name:<24} {model:<32} {tags}")
    print()
    print("  Pass --ensemble <comma-separated names> to override the NER encoder panel.")
    print("  Pass --spo-models <comma-separated names> to override the SPO model panel.\n")


def _list_runs(store: BenchmarkStore) -> None:
    """List available benchmark runs."""
    runs = store.list_runs()
    if not runs:
        print("  No benchmark runs found.")
        print(f"  Location: {store.runs_uri}")
        return
    print(f"\n  Available runs ({store.runs_uri}):")
    for name in runs:
        run = store.load_run(name)
        if run:
            report = run.load_report()
            if report:
                model_count = report.get("model_count", "?")
                generated = report.get("generated_at", "?")
                print(f"    {name}: {model_count} models, generated {generated}")
            else:
                extractions = run.list_extractions()
                print(f"    {name}: {len(extractions)} extractions (no report)")
        else:
            print(f"    {name}")
    if runs:
        print(f"\n    latest -> {runs[-1]}")


def _score_latest(store: BenchmarkStore) -> None:
    """Re-score the latest run against active ground truth, rebuild report."""
    gt = store.load_ground_truth("active")
    if not gt:
        print("  ERROR: No active ground truth. Generate with --ensemble-gt first.")
        return

    run = store.load_run("latest")
    if not run:
        print("  ERROR: No runs found. Run benchmarks first.")
        return

    # Load all extractions from the latest run (or legacy)
    model_names = run.list_extractions() or store.list_extractions()
    if not model_names:
        print("  ERROR: No extraction artifacts found.")
        return

    gt_mentions = []
    gt_propositions = []
    for chunk in gt["chunks"]:
        gt_mentions.extend(chunk["mentions"])
        gt_propositions.extend(chunk["propositions"])

    chunks_data = load_chunks()
    chunk_texts = {c["chunk_id"]: c["text"] for c in chunks_data} if chunks_data else None

    results = []
    for model in model_names:
        ext = run.load_extraction(model) or store.load_extraction(model)
        if not ext:
            continue
        scores = compute_model_scores(ext, gt_mentions, gt_propositions, chunk_texts)
        results.append({"model": model, "fixture": ext, "tags": [], "scores": scores})
        print(f"  {model}: strict_f1={scores['mention_strict_f1']:.4f}")

    report = build_report_json(results, ground_truth=gt, chunk_texts=chunk_texts, store=store)
    run.save_report(report)
    store.save_top_level_report(report)
    print(f"\n  Report updated: {store.top_report_uri}")


def main():
    parser = argparse.ArgumentParser(
        description="Unified extraction benchmark harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python tests/benchmark_harness.py                                  # interactive mode
  python tests/benchmark_harness.py --full                           # full methodology
  python tests/benchmark_harness.py --ensemble gliner-medium,gliner-large
  python tests/benchmark_harness.py --spo-models gemma3-12b,gpt-4o
  python tests/benchmark_harness.py --ensemble-only                  # NER bench only
  python tests/benchmark_harness.py --spo-only --run-id 2024-01-01-120000
  python tests/benchmark_harness.py --no-consensus                   # v3 fairness path
  python tests/benchmark_harness.py --ensemble-quorum 2             # override quorum
  python tests/benchmark_harness.py --ensemble-gt                    # GT only
  python tests/benchmark_harness.py --list-gt                        # show ground truths
  python tests/benchmark_harness.py --list-runs                      # show runs
  python tests/benchmark_harness.py --list-models                    # show configured models
  python tests/benchmark_harness.py --score                          # re-score latest
  python tests/benchmark_harness.py --clean                          # clean artifacts
""",
    )

    # ── Action flags ─────────────────────────────────────────────────
    action = parser.add_argument_group("actions")
    action.add_argument(
        "--full",
        action="store_true",
        help="Full methodology: run all models -> ensemble GT -> score -> report",
    )
    action.add_argument("--run", action="store_true", help="Run models (default when no action flag)")
    action.add_argument("--ensemble-gt", action="store_true", help="Generate ground truth via multi-model consensus")
    action.add_argument("--generate-gt", action="store_true", help="Generate ground truth from a single model")
    action.add_argument("--gt-model", type=str, default="gpt-4o", help="Model for single-model GT generation")
    action.add_argument("--score", action="store_true", help="Re-score latest run against active GT (no model runs)")
    action.add_argument("--report", action="store_true", help="Rebuild report JSON from latest run (no model runs)")
    action.add_argument("--list-gt", action="store_true", help="List available ground truth files")
    action.add_argument("--use-gt", type=str, metavar="NAME", help="Set active ground truth by name")
    action.add_argument("--list-runs", action="store_true", help="List timestamped benchmark runs")
    action.add_argument(
        "--list-models",
        action="store_true",
        help="List configured model names (use with --ensemble / --spo-models)",
    )
    action.add_argument("--clean", action="store_true", help="Clean cached artifacts (keep true fixtures)")
    action.add_argument("--view", action="store_true", help="Start the benchmark viewer SPA")

    # ── Configuration flags ──────────────────────────────────────────
    config = parser.add_argument_group("configuration")
    config.add_argument("--regen", action="store_true", help="Clear and regenerate all extraction artifacts")
    config.add_argument("--timeout", type=int, default=300, help="Per-model timeout in seconds (default: 300)")
    config.add_argument("--local-only", action="store_true", help="Skip cloud models")
    config.add_argument(
        "--all-videos",
        action="store_true",
        help="Run each model across every video in packages/media-ingest/tests/fixtures/audio_manifest.yaml "
        "(multi-video mode). Uses cached transcription/diarization, runs chunker + extraction per video. "
        "Default: single-video pytest fixture path.",
    )
    config.add_argument("--label", type=str, metavar="NAME", help="Label for this run (used in runs/ dir name)")
    config.add_argument(
        "--run-id",
        type=str,
        metavar="ID",
        help="Existing run ID to load (required by --spo-only)",
    )
    config.add_argument(
        "--chunk-size",
        type=int,
        metavar="TOKENS",
        help="Override chunk size in tokens (builds a ChunkConfig with this target)",
    )
    config.add_argument(
        "--sample-per-domain",
        type=int,
        metavar="N",
        default=None,
        help=(
            "Cap the number of chunks loaded from each medallion domain "
            "(media-ingest, congress-data, open-leaks). Default 50 in extraction "
            "tests. Pass 0 to disable the cap (use the full corpus — note open-leaks "
            "produces 3.6M+ chunks). Sets BENCH_SAMPLE_PER_DOMAIN for downstream subprocess."
        ),
    )
    config.add_argument(
        "--candidates",
        type=Path,
        metavar="PATH",
        default=Path(".test-output/gt-candidates.json"),
        help=(
            "Path to gt-candidates.json from scripts/benchmark/sample_gt_candidates.py. "
            "When the file exists, ensemble GT generation is restricted to those "
            "chunk_ids. Pass --no-candidates to disable. Default: .test-output/gt-candidates.json."
        ),
    )
    config.add_argument(
        "--no-candidates",
        dest="candidates",
        action="store_const",
        const=None,
        help="Run ensemble GT against the full chunk pool, ignoring gt-candidates.json.",
    )

    # ── Ensemble / SPO role-split flags ─────────────────────────────
    ensemble_group = parser.add_argument_group("ensemble / SPO role split")
    ensemble_group.add_argument(
        "--ensemble",
        type=str,
        metavar="LIST",
        help=(
            "Comma-separated encoder names that vote in Phase 1 NER consensus. "
            "Each also produces an individual per-model fixture for F1-vs-GT scoring. "
            "Default: all models with 'encoder' or 'extraction-specialist' tags."
        ),
    )
    ensemble_group.add_argument(
        "--spo-models",
        type=str,
        metavar="LIST",
        help=(
            "Comma-separated model names that consume consensus and run Phase 4 SPO. "
            "Default: all models with 'tier1', 'tier2', or 'cloud' tags."
        ),
    )
    ensemble_group.add_argument(
        "--ensemble-quorum",
        type=int,
        metavar="K",
        help=(
            "Override consensus quorum for ConsensusNode (default: ceil(N/2)). "
            "Must satisfy 1 ≤ K ≤ N where N is the number of --ensemble encoders."
        ),
    )

    # ── Phase-skip mutex group ───────────────────────────────────────
    phase_mutex = parser.add_mutually_exclusive_group()
    phase_mutex.add_argument(
        "--ensemble-only",
        action="store_true",
        help="Skip Phase 4 SPO entirely. Run Phase 1 NER consensus only; produce per-encoder + ensemble fixtures.",
    )
    phase_mutex.add_argument(
        "--spo-only",
        action="store_true",
        help=(
            "Skip Phase 1+2. Load cached consensus from --run-id's ClusterCache and run Phase 4 SPO only. "
            "Requires --run-id."
        ),
    )
    phase_mutex.add_argument(
        "--no-consensus",
        action="store_true",
        help=(
            "v3 fairness path: run each --ensemble model as a standalone NER+SPO pipeline "
            "(no consensus vote). Each produces its own NER + clusters + SPO."
        ),
    )

    # ── Deprecated (kept for backward compat) ────────────────────────
    config.add_argument("--generate-ground-truth", action="store_true", help=argparse.SUPPRESS)
    config.add_argument("--ground-truth-model", type=str, default="gpt-4o", help=argparse.SUPPRESS)

    args = parser.parse_args()

    # ── Post-parse validation ────────────────────────────────────────
    if args.spo_only and not args.run_id:
        parser.error("--spo-only requires --run-id <id>")

    # Propagate --sample-per-domain to the test_extraction_e2e fixture and any
    # subprocess we launch (the per-video extraction script reads from medallion
    # paths directly so it doesn't need this, but the in-process extraction
    # fixture in test_extraction_e2e does).
    if args.sample_per_domain is not None:
        os.environ["BENCH_SAMPLE_PER_DOMAIN"] = str(args.sample_per_domain)

    # Map deprecated flags
    if args.generate_ground_truth:
        args.generate_gt = True
        args.gt_model = args.ground_truth_model

    # Interactive mode when no flags given
    if len(sys.argv) == 1:
        args = _interactive_prompt()

    # --full implies --ensemble-gt (run everything in one shot)
    if args.full:
        args.ensemble_gt = True

    store = BenchmarkStore()

    # ── Quick info actions (no model runs) ───────────────────────────
    if args.list_gt:
        _list_ground_truths(store)
        return

    if args.list_runs:
        _list_runs(store)
        return

    if getattr(args, "list_models", False):
        _list_models()
        return

    if getattr(args, "use_gt", None):
        store.set_active_ground_truth(args.use_gt)
        print(f"  Active ground truth set to: {args.use_gt}")
        return

    if args.clean:
        cleaned = store.clean_all()
        for category, count in cleaned.items():
            print(f"  Cleaned {count} {category}")
        print("  Kept pipeline-cache and tests/fixtures/ (true fixtures).")
        return

    if getattr(args, "view", False):
        viewer_dir = ROOT / "packages" / "media-ingest" / "viewer-ui"
        print(f"  Starting viewer SPA from {viewer_dir}")
        print("  Open http://localhost:5173/viewer/benchmarks")
        subprocess.run(["npx", "vite"], cwd=str(viewer_dir))
        return

    if getattr(args, "score", False):
        _score_latest(store)
        return

    if getattr(args, "report", False):
        # Same as score but just rebuild report
        _score_latest(store)
        return

    # ── Resolve ensemble / SPO lists early (needed for --ensemble-gt standalone too) ──
    if getattr(args, "ensemble", None):
        ensemble_model_names = [n.strip() for n in args.ensemble.split(",")]
    else:
        ensemble_model_names = _default_ensemble()

    if not ensemble_model_names:
        parser.error("no ensemble encoders configured; pass --ensemble or check benchmark_config.py tags")

    ensemble_quorum: int | None = getattr(args, "ensemble_quorum", None)
    if ensemble_quorum is not None:
        n_ensemble = len(ensemble_model_names)
        if not (1 <= ensemble_quorum <= n_ensemble):
            parser.error(
                f"--ensemble-quorum {ensemble_quorum} out of range: "
                f"must satisfy 1 ≤ K ≤ {n_ensemble} (number of --ensemble encoders)"
            )

    if getattr(args, "spo_models", None):
        spo_model_names = [n.strip() for n in args.spo_models.split(",")]
    else:
        spo_model_names = _default_spo()

    # ── Ensemble GT-only mode (no model runs) ───────────────────────────────
    if args.ensemble_gt and not args.full:
        candidates = _load_candidate_chunk_ids(args.candidates)
        _run_ensemble_gt(
            store,
            ner_models=ensemble_model_names if getattr(args, "ensemble", None) else None,
            spo_models=spo_model_names if getattr(args, "spo_models", None) else None,
            candidates=candidates,
        )
        return

    # ── Synthetic model stub for the "ensemble" virtual model ────────────────
    # The ensemble consensus fixture from Phase A is always included in the
    # benchmark table as a synthetic entry so it shows up in F1 scoring.
    _ENSEMBLE_SYNTHETIC_CFG = ModelConfig(
        name="ensemble",
        model="ensemble",
        base_url="",
        structured_method="ensemble",
        tags=["encoder", "ensemble", "v4"],
    )

    # ── Determine which models to run based on mode ───────────────────────────
    if args.no_consensus:
        # v3 fairness: each ensemble encoder runs standalone NER+SPO
        from tests.benchmark_config import get_model_by_name as _get_model

        models = [_get_model(n) for n in ensemble_model_names if _get_model(n)]
        if not models:
            parser.error("--no-consensus: none of the --ensemble model names resolved in benchmark_config.py")
    elif args.ensemble_only:
        # NER bench only — Phase A encoders + synthetic ensemble fixture; no SPO models
        from tests.benchmark_config import get_model_by_name as _get_model

        encoder_models = [_get_model(n) for n in ensemble_model_names if _get_model(n)]
        models = encoder_models + [_ENSEMBLE_SYNTHETIC_CFG]
    elif args.spo_only:
        # Phase 4 SPO only — load cached ClusterCache from --run-id
        from tests.benchmark_config import get_model_by_name as _get_model

        models = [_get_model(n) for n in spo_model_names if _get_model(n)]
        if not models:
            parser.error("--spo-only: none of the --spo-models names resolved in benchmark_config.py")
    elif args.local_only:
        models = LOCAL_MODELS
    else:
        # Default: encoder panel + SPO models + ensemble virtual
        from tests.benchmark_config import get_model_by_name as _get_model

        encoder_models = [_get_model(n) for n in ensemble_model_names if _get_model(n)]
        spo_models_resolved = [_get_model(n) for n in spo_model_names if _get_model(n)]
        # Deduplicate (an encoder could also appear in spo_models)
        seen: set[str] = set()
        models = []
        for m in encoder_models + spo_models_resolved + [_ENSEMBLE_SYNTHETIC_CFG]:
            if m.name not in seen:
                seen.add(m.name)
                models.append(m)

    chunk_size_str = f"{args.chunk_size} tokens" if getattr(args, "chunk_size", None) else "default"
    # No automatic suffix on run IDs — ExGraph is the only pipeline now (CD-ys8n).
    # Pass --label NAME for a meaningful suffix on a specific run; otherwise
    # the run_id is just the bare timestamp (YYYY-MM-DD-HHMMSS).
    pipeline_label_str = "exgraph"
    pipeline_label = getattr(args, "label", None) or None

    # ── Catalyst data corpus footprint ──────────────────────────────────
    # Inventory the audio cache + manifest so the user sees what they're benchmarking against
    # before the slow per-model loop kicks off.
    manifest_path = ROOT / "packages" / "media-ingest" / "tests" / "fixtures" / "audio_manifest.yaml"
    manifest_videos: list[dict] = []
    if manifest_path.exists():
        try:
            import yaml as _yaml

            manifest_videos = (_yaml.safe_load(manifest_path.read_text()) or {}).get("videos", []) or []
        except Exception:
            manifest_videos = []
    cached_audio_doc_ids = [d for d in store.list_pipeline_cache_doc_ids() if d != "model_cache"]
    benchmark_chunks = load_chunks()

    n_local = sum(1 for m in models if "cloud" not in m.tags)
    n_cloud = sum(1 for m in models if "cloud" in m.tags)
    n_encoder = sum(1 for m in models if "encoder" in m.tags)

    # The --all-videos extraction subprocess (scripts/benchmark/bench_extract_per_video.py)
    # only walks media-ingest chunks per audio_manifest doc_id — it never touches
    # congress / open-leaks. Scope the displayed count accordingly so we don't
    # mislead the operator into thinking they're about to extract over 3.6M chunks.
    media_chunks_in_pool = [c for c in benchmark_chunks if (c.get("metadata") or {}).get("source") == "media_ingest"]
    if args.all_videos:
        scope_label = "multi-video (--all-videos · media-ingest)"
        active_video_label = f"{len(manifest_videos)} manifest video(s)"
        active_chunks = len(media_chunks_in_pool)
    else:
        scope_label = "single-video (demo_video)"
        active_video_label = "1 demo video (demo_video.mp4)"
        active_chunks = sum(
            1 for c in media_chunks_in_pool if c.get("document_id") in {"demo-video", "test-demo-video", ""}
        )

    # Create the run BEFORE rendering the header so the run_id can be included.
    run = store.create_run(label=pipeline_label)

    # Bind the unified event-stream writer to this run. Every harness /
    # exgraph / langgraph / dagster event for the lifetime of this process
    # appends to <local_cache_root>/events.jsonl — S3 doesn't support
    # append, so the live tail stays on local disk and is uploaded once
    # at run end via run.archive_events(). The viewer's bench routes
    # serve the live tail directly off the FastAPI; replays read the
    # archived copy from S3.
    events_path = store.local_cache_root / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("")  # forward-only: each run owns the live tail
    event_tail.configure(events_path, run_id=run.run_id)

    # Periodically upload the local tail to S3 so the StateInspector can
    # observe the run live (frontend polls /viewer/api/bench/runs/<id>/events.jsonl
    # every 3s; the viewer-api reads from S3). Without this the audit log
    # is blank until run end, which is brutal for full-corpus runs.
    # Default 5s; override via BENCH_EVENT_UPLOAD_INTERVAL_S.
    upload_interval_s = float(os.environ.get("BENCH_EVENT_UPLOAD_INTERVAL_S", "5"))

    def _upload_tail_to_s3(local_path: Path) -> None:
        store.client.put_object(run.events_key, local_path.read_bytes())

    event_tail.configure_periodic_upload(_upload_tail_to_s3, interval_s=upload_interval_s)

    # Spin up the run-bus so the viewer's LiveGantt can subscribe to live
    # events. Discovery: <local_cache_root>/.bus-port — the viewer's bench
    # API reads this to forward the WebSocket port.
    from tests.shared.run_bus import RunBus

    bus = RunBus(events_path=events_path)
    bus.start()
    (store.local_cache_root / ".bus-port").write_text(str(bus.port))

    event_tail.append(
        source="harness",
        node_name="run_start",
        status="started",
        details={
            "pipeline": pipeline_label or "default",
            "model_count": len(models),
            "bus_port": bus.port,
        },
    )

    # Resolve every detail the operator might want before kicking off a multi-minute run.
    candidate_path = getattr(args, "candidates", None)
    candidate_ids = _load_candidate_chunk_ids(candidate_path) if candidate_path else None
    cloud_endpoint = next(
        (m.base_url for m in models if "cloud" in m.tags and m.base_url),
        os.environ.get("LLM_BASE_URL", "http://litellm.catalyst-llm.svc.cluster.local:4000/v1"),
    )
    local_endpoint = next(
        (m.base_url for m in models if "cloud" not in m.tags and m.base_url),
        os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    )
    embed_provider = os.environ.get("EMBEDDING_PROVIDER", "openai")
    embed_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    telemetry = os.environ.get("CATALYST_TELEMETRY", "0") in ("1", "true", "yes")
    sample_cap = os.environ.get("BENCH_SAMPLE_PER_DOMAIN") or "—"
    try:
        git_sha = (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except Exception:
        git_sha = "unknown"
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # ANSI gated on isatty so log files stay clean.
    _C = _ansi_palette(sys.stdout.isatty())

    banner = (
        f"{_C['m']}╭─────────────────────────────────────────────────────────────────────────╮{_C['x']}\n"
        f"{_C['m']}│{_C['x']} {_C['c']}╔═╗╔═╗╔╦╗╔═╗╦ ╦ ╦╔═╗╔╦╗{_C['x']}{_C['k']}-{_C['x']}{_C['c']}╔╦╗╔═╗╔╦╗╔═╗{_C['x']}    "
        f"{_C['y']}//{_C['x']} {_C['k']}extraction benchmark harness    {_C['x']}{_C['m']}│{_C['x']}\n"
        f"{_C['m']}│{_C['x']} {_C['c']}║  ╠═╣ ║ ╠═╣║ ╚╦╝╚═╗ ║ {_C['x']}{_C['k']} {_C['x']}{_C['c']} ║║╠═╣ ║ ╠═╣{_C['x']}    "
        f"{_C['y']}//{_C['x']} {_C['k']}catalyst-dev / dj             {_C['x']}{_C['m']}│{_C['x']}\n"
        f"{_C['m']}│{_C['x']} {_C['c']}╚═╝╩ ╩ ╩ ╩ ╩╩═╝╩ ╚═╝ ╩ {_C['x']}{_C['k']}-{_C['x']}{_C['c']}═╩╝╩ ╩ ╩ ╩ ╩{_C['x']}    "
        f"{_C['y']}//{_C['x']} {_C['k']}{git_sha} · py{py_ver}        {_C['x']}{_C['m']}│{_C['x']}\n"
        f"{_C['m']}╰─────────────────────────────────────────────────────────────────────────╯{_C['x']}"
    )
    print()
    print(banner)

    def _row(label: str, value: str, *, accent: str = _C["e"]) -> str:
        return f"  {_C['k']}{label:<11}{_C['x']}{accent}{value}{_C['x']}"

    print()
    print(_row("run_id", run.run_id, accent=_C["c"]))
    print(_row("pipeline", f"{pipeline_label_str}  ·  label={pipeline_label or '(auto)'}"))
    print(
        _row(
            "models",
            f"{len(models)}  ·  {n_encoder} encoder · {n_local - n_encoder} local-llm · {n_cloud} cloud",
        )
    )
    print(
        _row(
            "corpus",
            f"{active_video_label}  ·  {len(cached_audio_doc_ids)} audio-cached  ·  {active_chunks} chunks in scope",
        )
    )
    print(_row("scope", f"{scope_label}  ·  timeout={args.timeout}s  ·  regen={args.regen}"))
    print(_row("chunker", f"chunk_size={chunk_size_str}"))
    print(
        _row(
            "candidates",
            (
                f"{len(candidate_ids)} chunk_ids  ·  {candidate_path.relative_to(ROOT) if candidate_path and candidate_path.is_relative_to(ROOT) else candidate_path}"
                if candidate_ids
                else "(none — full pool)"
            ),
            accent=_C["y"] if candidate_ids else _C["k"],
        )
    )
    print(_row("llm cloud", cloud_endpoint, accent=_C["c"]))
    print(_row("llm local", local_endpoint, accent=_C["c"]))
    print(_row("embedding", f"{embed_provider}  ·  {embed_model}"))
    print(_row("sample cap", f"BENCH_SAMPLE_PER_DOMAIN={sample_cap}"))
    print(
        _row(
            "telemetry", f"CATALYST_TELEMETRY={'on' if telemetry else 'off'}", accent=_C["g"] if telemetry else _C["k"]
        )
    )
    print(_row("output", run.s3_uri))
    print()

    if args.regen:
        # Clear legacy extraction cached artifacts
        store.clean_extractions()
        print(f"  cleared extraction artifacts → {run.s3_uri}")
        print()

    # ── Phase A + per-model loop — wrapped in rich.Live two-pane TUI ────────
    # BenchLiveUI auto-detects TTY; non-TTY (CI / Tilt log pipes) falls back
    # to plain column-aligned printing equivalent to the old _row() output.
    sample_n_raw = os.environ.get("BENCH_SAMPLE_PER_DOMAIN", "50")
    _bench_sample_n: int | None = int(sample_n_raw) if sample_n_raw else None
    if _bench_sample_n == 0:
        _bench_sample_n = None

    sample_cap_display = f"{_bench_sample_n}/domain" if _bench_sample_n is not None else "full"

    # pipeline_label is the user-supplied label (may be None); pipeline_label_str
    # is the always-set pipeline name (e.g. "exgraph"). The TUI wants a string.
    _pipeline_display = f"{pipeline_label_str} · label={pipeline_label}" if pipeline_label else pipeline_label_str
    ui = BenchLiveUI(
        run_id=run.run_id,
        pipeline=_pipeline_display,
        models=models,
        sample_cap=sample_cap_display,
    )

    # Capture library log records (transformers warnings, sentence-transformers
    # progress, httpx errors, etc.) into the UI's log panel without losing
    # them from the underlying stderr — DequeHandler appends to the deque
    # alongside whatever the root logger is already doing.
    _deque_handler = DequeHandler(ui._log_buffer)
    _deque_handler.setFormatter(logging.Formatter("%(name)s  %(message)s"))
    logging.getLogger().addHandler(_deque_handler)

    results = []
    t0 = time.monotonic()

    with ui:
        # ── Phase A — NER ensemble + cluster ────────────────────────────────
        if args.spo_only:
            # --spo-only: skip Phase A entirely; load existing cluster cache from --run-id.
            # The ClusterCache is an in-process warm cache keyed by doc_id+text — loading
            # the run record primes the local cache file at its canonical path so
            # _phase_a_build_cluster_cache warm-hits on any subsequent call.
            ui.log(f"--spo-only: loading ClusterCache from run {args.run_id} (skipping Phase 1)…")
            _spo_only_run = store.load_run(args.run_id)
            if _spo_only_run is None:
                print(f"\n  ERROR: run '{args.run_id}' not found in store. Run --list-runs to see available runs.")
                return
            # Warm Phase A shared state from pre-saved ensemble fixture in the target run
            _ensemble_fixture = _spo_only_run.load_extraction("ensemble") or store.load_fixture("extraction_ensemble")
            if _ensemble_fixture is None:
                print(f"\n  ERROR: no ensemble fixture in run '{args.run_id}'. Re-run Phase A first.")
                return
            # Reconstruct minimal shared state so the LLM-tier SPO path works
            _shared_docs: list = []
            _shared_clusters: dict = {}
            _shared_mentions: dict = {}
            _per_encoder_by_doc: dict = {}
            _phase_a_encoder_names: set[str] = set()
            ui.log(f"--spo-only: run {args.run_id} loaded; running Phase 4 SPO only")
        else:
            ui.log("Phase A: building cluster cache (NER ensemble once per doc)…")
            t_phase_a = time.monotonic()
            _shared_docs, _shared_clusters, _shared_mentions, _per_encoder_by_doc = _phase_a_build_cluster_cache(
                sample_n=_bench_sample_n,
                ner_ref_model=os.environ.get("BENCH_NER_REF_MODEL"),
                store=store,
                ensemble_models=ensemble_model_names if getattr(args, "ensemble", None) else None,
                quorum=ensemble_quorum,
            )
            _phase_a_duration = time.monotonic() - t_phase_a
            _total_phase_a_mentions = sum(len(v) for v in _shared_mentions.values())
            _total_phase_a_clusters = sum(len(v) for v in _shared_clusters.values())
            # Collect the names of all encoders that participated in the Phase A ensemble
            # so _run_model can distinguish "encoder in panel" from "encoder not in panel".
            _phase_a_encoder_names: set[str] = set(_per_encoder_by_doc.keys())
            ui.log(
                f"Phase A complete: {len(_shared_docs)} docs, "
                f"{_total_phase_a_clusters} clusters, "
                f"{_total_phase_a_mentions} consensus mentions, "
                f"{len(_phase_a_encoder_names)} encoders "
                f"in {_phase_a_duration:.1f}s"
            )

            if args.ensemble_only:
                ui.log("--ensemble-only: Phase A complete. Skipping Phase 4 SPO.")

        # ── Phase B — per-model SPO fan-out ─────────────────────────────────
        for cfg in models:
            tier = BenchLiveUI._compute_tier(cfg.tags)

            # --ensemble-only: only run encoder-tier + synthetic ensemble; skip SPO LLMs
            _is_encoder_or_ensemble = (
                "encoder" in cfg.tags
                or "extraction-specialist" in cfg.tags
                or cfg.name == "ensemble"
                or cfg.model == "ensemble"
            )
            if args.ensemble_only and not _is_encoder_or_ensemble:
                ui.set_status(cfg.name, "skip")
                ui.log(f"[{cfg.name}] skipped — --ensemble-only")
                continue

            # Skip cloud if no key
            if "cloud" in cfg.tags:
                api_key = cfg.api_key or os.environ.get("LLM_API_KEY", "")
                if not api_key:
                    ui.set_status(cfg.name, "skip")
                    ui.log(f"[{cfg.name}] skipped — no API key")
                    continue

            # Encoder-tier models in the Phase A panel use the pre-saved fixture.
            # Don't treat them as "cached" in the usual sense — they always
            # hit the shortcut path in _run_model and never reach the endpoint
            # check below.  Skip the cache check to let _run_model do routing.
            _is_encoder_in_panel = (
                "encoder" in cfg.tags or "extraction-specialist" in cfg.tags
            ) and cfg.name in _phase_a_encoder_names
            _is_synthetic_ensemble = cfg.name == "ensemble" or cfg.model == "ensemble"

            # Check cache for LLM-tier models only (encoders always use Phase A output)
            if not _is_encoder_in_panel and not _is_synthetic_ensemble:
                cached = store.load_fixture(f"extraction_{cfg.model}")
                if cached and not args.regen:
                    results.append({"model": cfg.name, "fixture": cached, "tags": cfg.tags})
                    run.save_extraction(cfg.model, cached)
                    ui.set_status(cfg.name, "cached", fixture=cached)
                    ui.log(f"[{cfg.name}] loaded from cache")
                    continue

            # Check endpoint (LLM-tier only — encoders/ensemble have no HTTP endpoint)
            if not _is_encoder_in_panel and not _is_synthetic_ensemble and cfg.base_url and "cloud" not in cfg.tags:
                import urllib.request

                try:
                    urllib.request.urlopen(
                        urllib.request.Request(f"{cfg.base_url}/models", method="GET"),
                        timeout=3,
                    )
                except Exception:
                    ui.set_status(cfg.name, "no-endpt")
                    ui.log(f"[{cfg.name}] endpoint unreachable: {cfg.base_url}")
                    continue

            # Mark running
            ui.set_status(cfg.name, "running")
            ui.log(f"[{cfg.name}] starting ({tier})")
            event_tail.append(
                source="harness",
                node_name="model_run",
                status="started",
                model=cfg.name,
                details={"tier": tier, "tags": list(cfg.tags)},
            )
            t_model_start = time.monotonic()

            # Phase B routing:
            # - encoder in Phase A panel → _run_model returns pre-saved fixture (no SPO)
            # - synthetic "ensemble" → _run_model returns ensemble fixture (no SPO)
            # - LLM-tier → _run_model runs SPO fan-out via extract_with_shared_clusters
            # - --no-consensus → bypass shared clusters; force legacy full-pipeline path
            # - fallback (no shared clusters) → legacy full-pipeline path
            _use_phase_b = bool(_shared_clusters) and not args.no_consensus
            fixture = _run_model(
                cfg,
                args.timeout,
                store,
                run.run_id,
                all_videos=args.all_videos,
                shared_docs=_shared_docs if _use_phase_b else None,
                shared_clusters=_shared_clusters if _use_phase_b else None,
                shared_mentions=_shared_mentions if _use_phase_b else None,
                phase_a_encoder_names=_phase_a_encoder_names if not args.no_consensus else set(),
            )
            duration_s = time.monotonic() - t_model_start

            if fixture:
                results.append({"model": cfg.name, "fixture": fixture, "tags": cfg.tags})
                ui.set_status(cfg.name, "ok", fixture=fixture)
                stats = fixture.get("stats") or {}
                ui.log(
                    f"[{cfg.name}] ok  {stats.get('mention_count', 0)} mentions "
                    f"· {stats.get('assertion_count', 0)} spo  in {duration_s:.1f}s"
                )

                # Save to run directory
                run.save_extraction(cfg.model, fixture)

                event_tail.append(
                    source="harness",
                    node_name="model_run",
                    status="completed",
                    model=cfg.name,
                    details={"duration_s": duration_s, "stats": fixture.get("stats", {}) or {}},
                )

                _save_incremental_report(results, store)
            else:
                ui.set_status(cfg.name, "FAIL")
                ui.log(f"[{cfg.name}] FAILED after {duration_s:.1f}s")
                event_tail.append(
                    source="harness",
                    node_name="model_run",
                    status="error",
                    model=cfg.name,
                    details={"duration_s": duration_s, "reason": "no_fixture"},
                )

            # Unload local model from Ollama VRAM to free memory for next model
            if "cloud" not in cfg.tags and "encoder" not in cfg.tags:
                subprocess.run(["ollama", "stop", cfg.model], capture_output=True, timeout=10)

    # Detach the deque handler so it doesn't leak into post-run logging.
    logging.getLogger().removeHandler(_deque_handler)

    total_time = time.monotonic() - t0

    # Generate ensemble ground truth if --full (not in ensemble-only / spo-only modes)
    if args.ensemble_gt and not args.ensemble_only and not args.spo_only:
        # Use the resolved ensemble / spo lists so --ensemble / --spo-models flow into GT gen
        candidates = _load_candidate_chunk_ids(args.candidates)
        _run_ensemble_gt(
            store,
            ner_models=ensemble_model_names if getattr(args, "ensemble", None) else None,
            spo_models=spo_model_names if getattr(args, "spo_models", None) else None,
            candidates=candidates,
        )

    # Load ground truth and compute scores
    gt = store.load_ground_truth("active")
    chunks_data = load_chunks()
    chunk_texts = {c["chunk_id"]: c["text"] for c in chunks_data} if chunks_data else None

    if gt:
        gt_mentions = []
        gt_propositions = []
        for chunk in gt["chunks"]:
            gt_mentions.extend(chunk["mentions"])
            gt_propositions.extend(chunk["propositions"])

        for r in results:
            r["scores"] = compute_model_scores(r["fixture"], gt_mentions, gt_propositions, chunk_texts)

    # Build and save report
    report = build_report_json(results, ground_truth=gt, chunk_texts=chunk_texts, store=store)

    # Save to run directory and top level
    run.save_report(report)
    store.save_top_level_report(report)

    # Save run config (full snapshot via BenchmarkConfig)
    bench_config = BenchmarkConfig.from_args(args, models)
    run.save_run_config(
        {
            **bench_config.to_dict(),
            "pipeline": pipeline_label,
            "regen": args.regen,
        }
    )

    event_tail.append(
        source="harness",
        node_name="run_end",
        status="completed",
        details={"results": len(results), "models": len(models)},
    )

    # Give the bus a beat to flush the final event over WS, then shut it
    # down. The events.jsonl on disk remains the canonical replay source.
    time.sleep(0.5)
    bus.stop()

    # Stop the periodic S3 uploader (one final flush included) before the
    # canonical archive call below — avoids a benign double-PUT race on
    # the same key.
    event_tail.stop_periodic_upload(final_flush=True)

    # Archive the local events.jsonl to S3 so future replays of this
    # specific run survive a later harness invocation truncating the
    # local cache file at its own run start. The local file is left
    # in place — the run-bus may still be flushing for a beat.
    archived_key = run.archive_events()
    if archived_key:
        print(f"  events archived to {run.events_uri}")

    # ── Run footer — totals across the live table above ─────────────────
    n_ok = len(results)
    n_fail = len(models) - n_ok
    total_mentions = sum((r["fixture"].get("stats") or {}).get("mention_count", 0) for r in results)
    total_assertions = sum((r["fixture"].get("stats") or {}).get("assertion_count", 0) for r in results)
    total_chunks_processed = sum((r["fixture"].get("stats") or {}).get("chunk_count", 0) for r in results)
    total_extract_s = sum((r["fixture"].get("stats") or {}).get("duration_s", 0.0) for r in results)
    total_retries = sum(
        ((r["fixture"].get("stats") or {}).get("mention_retries", 0) or 0)
        + ((r["fixture"].get("stats") or {}).get("proposition_retries", 0) or 0)
        for r in results
    )
    total_errors = sum((r["fixture"].get("stats") or {}).get("errors", 0) or 0 for r in results)
    total_llm_calls = sum((r["fixture"].get("stats") or {}).get("llm_call_count", 0) or 0 for r in results)
    avg_tok_s = (
        (sum((r["fixture"].get("stats") or {}).get("tokens_per_sec", 0.0) for r in results) / n_ok) if n_ok else 0.0
    )

    print(f"\n{'═' * 78}")
    print(f"  catalyst-data │ run complete in {total_time:.0f}s ({total_time / 60:.1f}m wall clock)")
    print(f"{'─' * 78}")
    print(f"  results     {n_ok}/{len(models)} ok · {n_fail} skipped/failed")
    print(
        f"  extraction  {total_mentions} mentions · {total_assertions} assertions "
        f"· {total_chunks_processed} chunks processed"
    )
    print(
        f"  throughput  Σ {total_extract_s:.0f}s extract-time · avg {avg_tok_s:.0f} tok/s "
        f"· {total_llm_calls} LLM calls · {total_retries} retries · {total_errors} errors"
    )
    if gt:
        gt_label = f"{gt['reference_model']} ({'reviewed' if gt.get('manually_reviewed') else 'unreviewed'})"
        print(f"  groundtruth {gt_label}")
    else:
        print("  groundtruth not available (run with --generate-ground-truth)")
    print(f"  artifacts   {run.s3_uri}")
    print(f"  report      {store.top_report_uri}")
    print(f"  events      {run.events_uri}")
    print(f"{'═' * 78}")

    # Print the full report
    if results:
        print_benchmark_report(results)


if __name__ == "__main__":
    main()
