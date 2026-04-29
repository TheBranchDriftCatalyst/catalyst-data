"""Benchmark report builder — generates structured JSON for the viewer SPA.

Extracted from tests/test_extraction_benchmark.py to be shared by both
the benchmark harness and pytest tests.

Usage:
    from tests.shared.report import build_report_json
    report = build_report_json(results, ground_truth=gt, chunks=chunks, chunk_texts=chunk_texts)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.shared.extraction_scoring import score_mentions, score_propositions


def build_report_json(
    results: list[dict],
    *,
    ground_truth: dict | None = None,
    chunks: list[dict] | None = None,
    chunk_texts: dict[str, str] | None = None,
    benchmark_chunks_path: Path | None = None,
) -> dict:
    """Build a structured JSON report for the viewer-ui SPA.

    Args:
        results: List of dicts with keys: model, fixture, tags.
        ground_truth: Ground truth dict (optional). If provided, models are scored.
        chunks: Full chunk list (optional, for chunk domain info).
        chunk_texts: {chunk_id: text} mapping for span accuracy scoring.
        benchmark_chunks_path: Path to benchmark_chunks.json for domain labels.
            Falls back to tests/fixtures/benchmark_chunks.json.

    Returns:
        Report dict ready for JSON serialization.
    """
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
            }
        )

    # ── Chunk domains ────────────────────────────────────────────────
    if benchmark_chunks_path is None:
        benchmark_chunks_path = Path(__file__).resolve().parents[1] / "fixtures" / "benchmark_chunks.json"
    chunk_domains: dict[str, str] = {}
    if benchmark_chunks_path.exists():
        for c in json.loads(benchmark_chunks_path.read_text()):
            chunk_domains[c.get("chunk_id", "")] = c.get("metadata", {}).get("domain", "unknown")

    # ── Entity matrix ────────────────────────────────────────────────
    entity_rows: dict[str, dict] = {}
    for r in results:
        for m in r["fixture"].get("mentions", []):
            text = m.get("text", "").strip()
            if not text:
                continue
            if text not in entity_rows:
                entity_rows[text] = {"text": text, "consensus_type": "", "domain": "", "models": {}}
            entity_rows[text]["models"][r["model"]] = {
                "type": m.get("mention_type", "?"),
                "confidence": m.get("confidence", 0),
                "span_start": m.get("span_start"),
                "span_end": m.get("span_end"),
            }
            chunk_id = m.get("chunk_id", "")
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
    spo_rows: dict[str, dict] = {}
    for r in results:
        for a in r["fixture"].get("assertions", []):
            subj = a.get("subject_text", a.get("subject", "")).strip()
            pred = a.get("predicate", "").strip()
            obj = a.get("object_text", a.get("object", "")).strip()
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
                }
            if r["model"] not in spo_rows[key]["models"]:
                spo_rows[key]["models"].append(r["model"])

    for row in spo_rows.values():
        row["model_count"] = len(row["models"])
    propositions = sorted(spo_rows.values(), key=lambda x: -x["model_count"])

    # Domain summary
    domain_counts: dict[str, int] = {}
    if benchmark_chunks_path.exists():
        for c in json.loads(benchmark_chunks_path.read_text()):
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

        ground_truth_meta = {
            "available": True,
            "reference_model": ground_truth.get("reference_model", "unknown"),
            "manually_reviewed": ground_truth.get("manually_reviewed", False),
            "mention_count": len(gt_mentions),
            "proposition_count": len(gt_propositions),
        }

        for model_entry, r in zip(models, results, strict=False):
            ext = r["fixture"]
            m_scores = score_mentions(ext.get("mentions", []), gt_mentions, chunk_texts=chunk_texts)
            p_scores = score_propositions(ext.get("assertions", []), gt_propositions)
            model_entry["scores"] = {
                "mention_strict_precision": round(m_scores["strict_precision"], 4),
                "mention_strict_recall": round(m_scores["strict_recall"], 4),
                "mention_strict_f1": round(m_scores["strict_f1"], 4),
                "mention_relaxed_precision": round(m_scores["relaxed_precision"], 4),
                "mention_relaxed_recall": round(m_scores["relaxed_recall"], 4),
                "mention_relaxed_f1": round(m_scores["relaxed_f1"], 4),
                "mention_type_accuracy": round(m_scores["type_accuracy"], 4),
                "mention_span_accuracy": round(m_scores["span_accuracy"], 4)
                if m_scores["span_accuracy"] is not None
                else 0.0,
                "proposition_strict_precision": round(p_scores["strict_precision"], 4),
                "proposition_strict_recall": round(p_scores["strict_recall"], 4),
                "proposition_strict_f1": round(p_scores["strict_f1"], 4),
                "proposition_relaxed_precision": round(p_scores["relaxed_precision"], 4),
                "proposition_relaxed_recall": round(p_scores["relaxed_recall"], 4),
                "proposition_relaxed_f1": round(p_scores["relaxed_f1"], 4),
                "hallucination_rate": round(1.0 - m_scores["span_accuracy"], 4)
                if m_scores["span_accuracy"] is not None
                else None,
                "quality_speed_ratio": round(m_scores["strict_f1"] / max(model_entry["stats"]["duration_s"], 0.1), 4),
                "per_chunk_latency": round(
                    model_entry["stats"]["duration_s"] / max(model_entry["stats"]["chunk_count"], 1), 4
                ),
            }

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
