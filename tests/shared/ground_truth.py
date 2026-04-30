"""Ensemble ground truth generation via multi-model consensus.

Extracted from tests/test_extraction_benchmark.py to be shared by both
the benchmark harness and pytest tests.

Usage:
    from tests.shared.ground_truth import generate_ensemble_ground_truth
    from tests.shared.store import BenchmarkStore

    store = BenchmarkStore()
    gt = generate_ensemble_ground_truth(store)
"""

from __future__ import annotations

from collections import Counter

from tests.benchmark_config import NER_ENSEMBLE_MODELS, SPO_ENSEMBLE_MODELS
from tests.shared.store import BenchmarkStore


def _build_mentions_by_chunk(extraction: dict) -> dict[str, list[dict]]:
    """Index an extraction fixture's mentions by chunk_id."""
    by_chunk: dict[str, list[dict]] = {}
    for m in extraction.get("mentions", []):
        cid = m.get("chunk_id", "")
        by_chunk.setdefault(cid, []).append(m)
    return by_chunk


def _build_assertions_by_chunk(extraction: dict) -> dict[str, list[dict]]:
    """Index an extraction fixture's assertions by chunk_id."""
    by_chunk: dict[str, list[dict]] = {}
    for a in extraction.get("assertions", []):
        prov = a.get("provenance") or {}
        cid = prov.get("chunk_id", "")
        by_chunk.setdefault(cid, []).append(a)
    return by_chunk


def _load_available_extractions(store: BenchmarkStore, model_list: list[str]) -> dict[str, dict]:
    """Load extraction fixtures for models that have cached results."""
    available = {}
    for model in model_list:
        ext = store.load_fixture(f"extraction_{model}")
        if ext is not None:
            available[model] = ext
    return available


def _ner_consensus(
    all_model_mentions: dict[str, list[dict]],
    source_text: str,
    threshold: int,
    ensemble_size: int | None = None,
) -> list[dict]:
    """Compute NER consensus mentions via majority voting.

    A mention is accepted if >= threshold models agree on
    (normalized_text, mention_type). Spans are recomputed deterministically.
    """
    from catalyst_exgraph.nodes.spans import find_best_span

    # Collect votes: (norm_text, norm_type) -> {model: mention}
    votes: dict[tuple[str, str], dict[str, dict]] = {}
    for model_name, mentions in all_model_mentions.items():
        for m in mentions:
            text = m.get("text", "").strip()
            mtype = m.get("mention_type", "").upper().strip()
            if not text:
                continue
            key = (text.lower().strip(), mtype)
            votes.setdefault(key, {})
            if model_name not in votes[key]:
                votes[key][model_name] = m

    accepted = []
    for (_norm_text, _norm_type), model_entries in votes.items():
        if len(model_entries) < threshold:
            continue

        type_counts: Counter[str] = Counter()
        for m in model_entries.values():
            type_counts[m.get("mention_type", "").upper().strip()] += 1
        best_type = type_counts.most_common(1)[0][0]

        first_mention = next(iter(model_entries.values()))
        original_text = first_mention.get("text", "").strip()

        span_start, span_end = find_best_span(source_text, original_text)
        # Confidence = fraction of the full ensemble that agreed (not just chunk-local models)
        total = ensemble_size if ensemble_size else len(all_model_mentions)
        confidence = round(len(model_entries) / total, 2)

        accepted.append(
            {
                "text": original_text,
                "mention_type": best_type,
                "span_start": span_start,
                "span_end": span_end,
                "confidence": confidence,
            }
        )

    return accepted


def _spo_consensus(
    all_model_assertions: dict[str, list[dict]],
    threshold: int,
    ensemble_size: int | None = None,
) -> list[dict]:
    """Compute SPO consensus propositions via majority voting.

    A proposition is accepted if >= threshold models agree on
    (normalized_subject, predicate, normalized_object).
    """
    votes: dict[tuple[str, str, str], dict[str, dict]] = {}
    for model_name, assertions in all_model_assertions.items():
        for a in assertions:
            subj = (a.get("subject") or a.get("subject_text") or "").strip()
            pred = a.get("predicate", "").strip()
            obj = (a.get("object") or a.get("object_text") or "").strip()
            if not (subj and pred and obj):
                continue
            key = (subj.lower(), pred.lower(), obj.lower())
            votes.setdefault(key, {})
            if model_name not in votes[key]:
                votes[key][model_name] = a

    accepted = []
    for (_norm_subj, _norm_pred, _norm_obj), model_entries in votes.items():
        if len(model_entries) < threshold:
            continue

        first_a = next(iter(model_entries.values()))
        subject = (first_a.get("subject") or first_a.get("subject_text") or "").strip()
        predicate = first_a.get("predicate", "").strip()
        obj_text = (first_a.get("object") or first_a.get("object_text") or "").strip()

        total = ensemble_size if ensemble_size else len(all_model_assertions)
        confidence = round(len(model_entries) / total, 2)

        accepted.append(
            {
                "subject": subject,
                "predicate": predicate,
                "object": obj_text,
                "confidence": confidence,
                "evidence": "",
            }
        )

    return accepted


def generate_ensemble_ground_truth(
    store: BenchmarkStore | None = None,
    ner_models: list[str] | None = None,
    spo_models: list[str] | None = None,
    exclude_model: str | None = None,
    ner_threshold: int | None = None,
    spo_threshold: int | None = None,
) -> dict | None:
    """Generate ground truth from multi-model consensus.

    Args:
        store: BenchmarkStore instance for loading fixtures. If None, creates default.
        ner_models: Models to use for NER consensus. Defaults to NER_ENSEMBLE_MODELS.
        spo_models: Models to use for SPO consensus. Defaults to SPO_ENSEMBLE_MODELS.
        exclude_model: If set, exclude this model from the ensemble (leave-one-out).
            Used during scoring to avoid tautological self-grading — a model
            should not vote on the ground truth it is scored against.
        ner_threshold: Minimum NER votes required. Defaults to strict majority (N//2+1).
        spo_threshold: Minimum SPO votes required. Defaults to strict majority (N//2+1).

    Returns:
        Ground truth dict matching the standard fixture format, or None if
        fewer than 2 models have fixtures available.
    """
    if store is None:
        store = BenchmarkStore()
    if ner_models is None:
        ner_models = NER_ENSEMBLE_MODELS
    if spo_models is None:
        spo_models = SPO_ENSEMBLE_MODELS

    # Load chunks — prefer benchmark subset (cross-domain) over pipeline cache (media-only)
    chunks = store.load_benchmark_chunks() or store.load_chunks()
    if not chunks:
        print("  ERROR: No chunks fixture. Run test_pipeline_integration.py first.")
        return None

    # Load available extraction fixtures
    ner_extractions = _load_available_extractions(store, ner_models)
    spo_extractions = _load_available_extractions(store, spo_models)

    # Leave-one-out: exclude the model being scored to avoid tautological bias
    if exclude_model:
        # Match by model name from the extraction fixture, not the config key
        ner_extractions = {
            k: v for k, v in ner_extractions.items() if k != exclude_model and v.get("model") != exclude_model
        }
        spo_extractions = {
            k: v for k, v in spo_extractions.items() if k != exclude_model and v.get("model") != exclude_model
        }

    if len(ner_extractions) < 2:
        print(
            f"  Not enough NER model fixtures ({len(ner_extractions)} available, need >= 2). "
            f"Looked for: {', '.join(ner_models)}"
        )
        return None

    if len(spo_extractions) < 2:
        print(
            f"  WARNING: Only {len(spo_extractions)} SPO model fixture(s) available "
            f"(looked for: {', '.join(spo_models)}). "
            f"SPO consensus will be weak."
        )

    # Compute thresholds: default is strict majority (N//2+1), overridable
    if ner_threshold is None:
        ner_threshold = len(ner_extractions) // 2 + 1
    if spo_threshold is None:
        spo_threshold = len(spo_extractions) // 2 + 1 if spo_extractions else 1

    print("\n  Ensemble ground truth configuration:")
    print(f"    NER models ({len(ner_extractions)}): {', '.join(ner_extractions.keys())}")
    print(f"    NER threshold: >= {ner_threshold} of {len(ner_extractions)} models must agree")
    print(f"    SPO models ({len(spo_extractions)}): {', '.join(spo_extractions.keys())}")
    print(f"    SPO threshold: >= {spo_threshold} of {len(spo_extractions)} models must agree")

    # Pre-index mentions and assertions by chunk for each model
    ner_by_chunk: dict[str, dict[str, list[dict]]] = {}
    for model, ext in ner_extractions.items():
        ner_by_chunk[model] = _build_mentions_by_chunk(ext)

    spo_by_chunk: dict[str, dict[str, list[dict]]] = {}
    for model, ext in spo_extractions.items():
        spo_by_chunk[model] = _build_assertions_by_chunk(ext)

    # Build per-chunk ground truth via consensus
    gt_chunks = []
    total_mention_candidates = 0
    total_spo_candidates = 0

    for chunk in chunks:
        cid = chunk["chunk_id"]
        source_text = chunk["text"]

        chunk_model_mentions: dict[str, list[dict]] = {}
        for model in ner_extractions:
            model_mentions = ner_by_chunk[model].get(cid, [])
            if model_mentions:
                chunk_model_mentions[model] = model_mentions
                total_mention_candidates += len(model_mentions)

        chunk_model_assertions: dict[str, list[dict]] = {}
        for model in spo_extractions:
            model_assertions = spo_by_chunk[model].get(cid, [])
            if model_assertions:
                chunk_model_assertions[model] = model_assertions
                total_spo_candidates += len(model_assertions)

        consensus_mentions = (
            _ner_consensus(chunk_model_mentions, source_text, ner_threshold, ensemble_size=len(ner_extractions))
            if chunk_model_mentions
            else []
        )
        consensus_propositions = (
            _spo_consensus(chunk_model_assertions, spo_threshold, ensemble_size=len(spo_extractions))
            if chunk_model_assertions
            else []
        )

        gt_chunks.append(
            {
                "chunk_id": cid,
                "text": source_text,
                "mentions": consensus_mentions,
                "propositions": consensus_propositions,
            }
        )

    total_mentions = sum(len(c["mentions"]) for c in gt_chunks)
    total_propositions = sum(len(c["propositions"]) for c in gt_chunks)

    all_models_used = sorted(set(list(ner_extractions.keys()) + list(spo_extractions.keys())))
    reference_label = f"ensemble({','.join(all_models_used)})"

    ground_truth = {
        "domain": "media_ingest",
        "reference_model": reference_label,
        "manually_reviewed": False,
        "chunk_count": len(gt_chunks),
        "total_mentions": total_mentions,
        "total_propositions": total_propositions,
        "ensemble_config": {
            "ner_models": list(ner_extractions.keys()),
            "spo_models": list(spo_extractions.keys()),
            "threshold": "majority",
            "ner_threshold": ner_threshold,
            "spo_threshold": spo_threshold,
            "ner_candidates": total_mention_candidates,
            "spo_candidates": total_spo_candidates,
        },
        "chunks": gt_chunks,
    }

    return ground_truth
