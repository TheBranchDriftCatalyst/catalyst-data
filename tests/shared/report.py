"""Benchmark report builder — generates structured JSON for the viewer SPA.

Extracted from tests/test_extraction_benchmark.py to be shared by both
the benchmark harness and pytest tests.

Usage:
    from tests.shared.report import build_report_json
    report = build_report_json(results, ground_truth=gt, chunks=chunks, chunk_texts=chunk_texts)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tests.shared.extraction_scoring import (
    _assertion_object,
    _assertion_subject,
    _mention_chunk_id,
    _mention_type,
    compute_model_scores,
    score_provenance,
)

if TYPE_CHECKING:
    from tests.shared.store import BenchmarkStore


def build_report_json(
    results: list[dict],
    *,
    ground_truth: dict | None = None,
    chunks: list[dict] | None = None,
    chunk_texts: dict[str, str] | None = None,
    store: BenchmarkStore | None = None,
) -> dict:
    """Build a structured JSON report for the viewer-ui SPA.

    Args:
        results: List of dicts with keys: model, fixture, tags.
        ground_truth: Ground truth dict (optional). If provided, models are scored.
        chunks: Full chunk list (optional, for chunk domain info).
        chunk_texts: {chunk_id: text} mapping for span accuracy scoring.
        store: BenchmarkStore for leave-one-out GT regeneration. If None,
            ensemble models are scored against the same GT (legacy behavior).

    Returns:
        Report dict ready for JSON serialization.
    """
    # Respect a caller-supplied ``chunks`` list (used by unit tests that
    # don't have a live MinIO/S3 endpoint). When ``chunks`` is ``None`` we
    # fall back to loading from the medallion bucket the harness already
    # populated.
    if chunks is not None:
        medallion_chunks = chunks
    else:
        from tests.shared.medallion import load_chunks

        medallion_chunks = load_chunks()
    # ── Models summary ───────────────────────────────────────────────
    models = []
    for r in results:
        s = r["fixture"].get("stats", {})
        tags = r.get("tags", [])
        if "encoder" in tags:
            model_type = "encoder"
        elif "extraction-specialist" in tags:
            model_type = "specialist"
        else:
            model_type = "llm"

        # Score provenance completeness
        ext = r["fixture"]
        prov_scores = score_provenance(
            ext.get("mentions", []),
            ext.get("assertions", []),
        )

        models.append(
            {
                "name": r["model"],
                "type": model_type,
                "tags": tags,
                "stats": {
                    "mention_count": s.get("mention_count", 0),
                    "assertion_count": s.get("assertion_count", 0),
                    "duration_s": s.get("duration_s", 0),
                    "tokens_per_sec": s.get("tokens_per_sec", 0),
                    "mention_retries": s.get("mention_retries", 0),
                    "proposition_retries": s.get("proposition_retries", 0),
                    "errors": s.get("errors", 0),
                    "chunk_count": s.get("chunk_count", 0),
                },
                "pipeline": s.get("pipeline", {}),
                "provenance": prov_scores,
            }
        )

    # ── Chunk domains ────────────────────────────────────────────────
    chunk_domains: dict[str, str] = {
        c.get("chunk_id", ""): c.get("metadata", {}).get("domain", "unknown") for c in medallion_chunks
    }

    # ── Entity matrix ────────────────────────────────────────────────
    entity_rows: dict[str, dict] = {}
    for r in results:
        for m in r["fixture"].get("mentions", []):
            text = m.get("text", "").strip()
            if not text:
                continue
            if text not in entity_rows:
                entity_rows[text] = {
                    "text": text,
                    "consensus_type": "",
                    "domain": "",
                    "models": {},
                    "mentions": [],  # per-occurrence provenance for the side-panel detail view
                }
            # Wave-1 wire shape: Mention.canonical_type + provenance.chunk_id.
            # The accessor helpers fall back to the legacy keys so this
            # builder works against both predicted fixtures (new) and any
            # remaining legacy on-disk fixtures.
            mtype = _mention_type(m) or "?"
            entity_rows[text]["models"][r["model"]] = {
                "type": mtype,
                "confidence": m.get("confidence") or m.get("mean_confidence") or 0,
                "span_start": m.get("span_start"),
                "span_end": m.get("span_end"),
            }
            # Preserve full per-mention provenance — one entry per source mention
            # across (model, chunk). The aggregated `models` map drives the
            # consensus matrix; `mentions` drives EntityJsonPanel's detail view.
            prov = m.get("provenance") or {}
            chunk_id = _mention_chunk_id(m)
            entity_rows[text]["mentions"].append(
                {
                    "model": r["model"],
                    "type": mtype,
                    "chunk_id": chunk_id,
                    "document_id": m.get("document_id", "") or prov.get("source_document_id", ""),
                    "span_start": m.get("span_start"),
                    "span_end": m.get("span_end"),
                    "confidence": m.get("confidence") or m.get("mean_confidence") or 0,
                    "context": m.get("context", ""),
                    "temporal_start_ms": prov.get("temporal_start_ms"),
                    "temporal_end_ms": prov.get("temporal_end_ms"),
                    "speaker_label": prov.get("speaker_label"),
                    "source_media_uri": prov.get("source_media_uri"),
                }
            )
            if chunk_id and not entity_rows[text]["domain"]:
                entity_rows[text]["domain"] = chunk_domains.get(chunk_id, "unknown")

    for row in entity_rows.values():
        type_counts: dict[str, int] = {}
        for info in row["models"].values():
            t = info["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        row["consensus_type"] = max(type_counts, key=type_counts.get) if type_counts else "?"
        row["model_count"] = len(row["models"])

    entities = sorted(entity_rows.values(), key=lambda x: -x["model_count"])

    # ── SPO matrix ───────────────────────────────────────────────────
    # Predicted assertions use the Wave-1 ``catalyst_contracts_core.Assertion``
    # shape (subject_text / object_text + AMR-rich fields). The accessor
    # helpers fall back to the legacy ``subject`` / ``object`` keys for any
    # surviving on-disk fixtures from the SPO era.
    spo_rows: dict[str, dict] = {}
    for r in results:
        for a in r["fixture"].get("assertions", []):
            subj = _assertion_subject(a).strip()
            pred = a.get("predicate", "").strip()
            obj = _assertion_object(a).strip()
            if not (subj and pred and obj):
                continue
            key = f"{subj}|{pred}|{obj}"
            if key not in spo_rows:
                prov = a.get("provenance") or {}
                chunk_id = prov.get("chunk_id", "")
                spo_rows[key] = {
                    "subject": subj,
                    "predicate": pred,
                    "object": obj,
                    "domain": chunk_domains.get(chunk_id, "unknown"),
                    "models": [],
                    # AMR-rich fields surfaced so report consumers (e.g. the
                    # bench viewer SPA) can show graph-native semantics
                    # alongside the flat SPO triple.
                    "amr_frame": a.get("amr_frame"),
                    "polarity": a.get("polarity", True),
                    "modality": a.get("modality"),
                    "qualifiers": a.get("qualifiers") or {},
                }
            if r["model"] not in spo_rows[key]["models"]:
                spo_rows[key]["models"].append(r["model"])

    for row in spo_rows.values():
        row["model_count"] = len(row["models"])
    propositions = sorted(spo_rows.values(), key=lambda x: -x["model_count"])

    # Domain summary
    domain_counts: dict[str, int] = {}
    for c in medallion_chunks:
        d = c.get("metadata", {}).get("domain", "unknown")
        domain_counts[d] = domain_counts.get(d, 0) + 1

    # ── Ground truth scoring ────────────────────────────────────────
    ground_truth_meta: dict = {"available": False}
    if ground_truth is not None:
        gt_mentions: list[dict] = []
        gt_propositions: list[dict] = []
        for chunk in ground_truth.get("chunks", []):
            gt_mentions.extend(chunk.get("mentions", []))
            gt_propositions.extend(chunk.get("propositions", []))

        # Identify ensemble member models for leave-one-out scoring
        ensemble_config = ground_truth.get("ensemble_config", {})
        ensemble_ner_models = set(ensemble_config.get("ner_models", []))
        ensemble_spo_models = set(ensemble_config.get("spo_models", []))
        ensemble_all = ensemble_ner_models | ensemble_spo_models

        # Cache leave-one-out GTs to avoid regenerating for the same model
        loo_cache: dict[str, dict] = {}

        ground_truth_meta = {
            "available": True,
            "reference_model": ground_truth.get("reference_model", "unknown"),
            "manually_reviewed": ground_truth.get("manually_reviewed", False),
            "mention_count": len(gt_mentions),
            "proposition_count": len(gt_propositions),
        }

        for model_entry, r in zip(models, results, strict=False):
            ext = r["fixture"]
            model_name = r["model"]

            # Leave-one-out: if this model is in the ensemble, regenerate GT without it
            score_gt_mentions = gt_mentions
            score_gt_propositions = gt_propositions
            if store and model_name in ensemble_all and not ground_truth.get("manually_reviewed"):
                if model_name not in loo_cache:
                    from tests.shared.ground_truth import generate_ensemble_ground_truth

                    loo_gt = generate_ensemble_ground_truth(
                        store,
                        ner_models=list(ensemble_ner_models),
                        spo_models=list(ensemble_spo_models),
                        exclude_model=model_name,
                    )
                    loo_cache[model_name] = loo_gt or {}
                loo_gt = loo_cache[model_name]
                if loo_gt and loo_gt.get("chunks"):
                    score_gt_mentions = []
                    score_gt_propositions = []
                    for chunk in loo_gt["chunks"]:
                        score_gt_mentions.extend(chunk.get("mentions", []))
                        score_gt_propositions.extend(chunk.get("propositions", []))

            model_entry["scores"] = compute_model_scores(
                ext, score_gt_mentions, score_gt_propositions, chunk_texts=chunk_texts
            )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "model_count": len(models),
        "entity_count": len(entities),
        "proposition_count": len(propositions),
        "model_names": [r["model"] for r in results],
        "domains": domain_counts,
        "ground_truth": ground_truth_meta,
        "models": models,
        "entities": entities,
        "propositions": propositions,
    }
