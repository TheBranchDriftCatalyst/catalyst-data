"""Extraction quality scoring — mention F1 and proposition F1.

Standalone module with no Dagster dependencies. Used by the benchmark
tests to compare model extraction output against ground truth.

Usage:
    from tests.shared.extraction_scoring import score_mentions, score_propositions

    result = score_mentions(predicted, ground_truth, source_text)
    print(f"Mention F1: {result['f1']:.3f}")
"""

from __future__ import annotations


def _normalize(text: str) -> str:
    """Lowercase + strip for fuzzy matching."""
    return text.strip().lower()


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def validate_ground_truth(ground_truth: list[dict], source_text: str) -> list[str]:
    """Self-check: verify all ground truth mention spans are correct.

    Returns list of error messages (empty = all valid).
    """
    errors = []
    for i, m in enumerate(ground_truth):
        text = m.get("text", "")
        span_start = m.get("span_start")
        span_end = m.get("span_end")
        if span_start is None or span_end is None:
            continue
        if span_start < 0 or span_end > len(source_text) or span_end <= span_start:
            errors.append(f"[{i}] Invalid span [{span_start}:{span_end}] for source text of length {len(source_text)}")
            continue
        actual = source_text[span_start:span_end]
        if actual != text:
            errors.append(f"[{i}] Span mismatch: mention text '{text}' != source[{span_start}:{span_end}] = '{actual}'")
    return errors


def score_mentions(
    predicted: list[dict],
    ground_truth: list[dict],
    source_text: str | None = None,
) -> dict:
    """Compare predicted mentions against ground truth.

    Matching modes:
    - strict: text + mention_type both match
    - relaxed: text matches (type may differ)

    Returns dict with precision, recall, f1 (strict and relaxed),
    type_accuracy, span_accuracy, and per-mention details.
    """
    gt_strict = {(_normalize(m["text"]), m.get("mention_type", "").upper()) for m in ground_truth}
    gt_relaxed = {_normalize(m["text"]) for m in ground_truth}

    pred_strict = {(_normalize(m["text"]), m.get("mention_type", "").upper()) for m in predicted}
    pred_relaxed = {_normalize(m["text"]) for m in predicted}

    # Strict match (text + type)
    strict_tp = len(pred_strict & gt_strict)
    strict_precision = strict_tp / len(pred_strict) if pred_strict else 0.0
    strict_recall = strict_tp / len(gt_strict) if gt_strict else 0.0
    strict_f1 = _f1(strict_precision, strict_recall)

    # Relaxed match (text only)
    relaxed_tp = len(pred_relaxed & gt_relaxed)
    relaxed_precision = relaxed_tp / len(pred_relaxed) if pred_relaxed else 0.0
    relaxed_recall = relaxed_tp / len(gt_relaxed) if gt_relaxed else 0.0
    relaxed_f1 = _f1(relaxed_precision, relaxed_recall)

    # Type accuracy: among text-matched mentions, what fraction have correct type
    type_correct = 0
    type_total = 0
    gt_by_text = {_normalize(m["text"]): m.get("mention_type", "").upper() for m in ground_truth}
    for m in predicted:
        norm = _normalize(m["text"])
        if norm in gt_by_text:
            type_total += 1
            if m.get("mention_type", "").upper() == gt_by_text[norm]:
                type_correct += 1
    type_accuracy = type_correct / type_total if type_total else 0.0

    # Span accuracy: for predicted mentions with spans, check source_text alignment
    span_correct = 0
    span_total = 0
    if source_text:
        for m in predicted:
            s, e = m.get("span_start"), m.get("span_end")
            if s is not None and e is not None and 0 <= s < e <= len(source_text):
                span_total += 1
                if source_text[s:e] == m.get("text", ""):
                    span_correct += 1
    span_accuracy = span_correct / span_total if span_total else 0.0

    # Detail: what was missed and what was extra
    missed = gt_relaxed - pred_relaxed
    extra = pred_relaxed - gt_relaxed

    return {
        "strict_precision": strict_precision,
        "strict_recall": strict_recall,
        "strict_f1": strict_f1,
        "relaxed_precision": relaxed_precision,
        "relaxed_recall": relaxed_recall,
        "relaxed_f1": relaxed_f1,
        "type_accuracy": type_accuracy,
        "span_accuracy": span_accuracy,
        "predicted_count": len(predicted),
        "ground_truth_count": len(ground_truth),
        "strict_tp": strict_tp,
        "relaxed_tp": relaxed_tp,
        "missed": sorted(missed),
        "extra": sorted(extra),
    }


def score_propositions(
    predicted: list[dict],
    ground_truth: list[dict],
) -> dict:
    """Compare predicted propositions against ground truth.

    Matching modes:
    - strict: subject + predicate + object all match (normalized)
    - relaxed: subject + object match, predicate ignored

    Returns dict with precision, recall, f1, and per-proposition details.
    """

    def _triple_strict(p: dict) -> tuple:
        return (
            _normalize(p.get("subject", p.get("subject_text", ""))),
            _normalize(p.get("predicate", "")),
            _normalize(p.get("object", p.get("object_text", ""))),
        )

    def _triple_relaxed(p: dict) -> tuple:
        return (
            _normalize(p.get("subject", p.get("subject_text", ""))),
            _normalize(p.get("object", p.get("object_text", ""))),
        )

    gt_strict = {_triple_strict(p) for p in ground_truth}
    gt_relaxed = {_triple_relaxed(p) for p in ground_truth}

    pred_strict = {_triple_strict(p) for p in predicted}
    pred_relaxed = {_triple_relaxed(p) for p in predicted}

    # Strict
    strict_tp = len(pred_strict & gt_strict)
    strict_precision = strict_tp / len(pred_strict) if pred_strict else 0.0
    strict_recall = strict_tp / len(gt_strict) if gt_strict else 0.0
    strict_f1 = _f1(strict_precision, strict_recall)

    # Relaxed (subject + object only)
    relaxed_tp = len(pred_relaxed & gt_relaxed)
    relaxed_precision = relaxed_tp / len(pred_relaxed) if pred_relaxed else 0.0
    relaxed_recall = relaxed_tp / len(gt_relaxed) if gt_relaxed else 0.0
    relaxed_f1 = _f1(relaxed_precision, relaxed_recall)

    missed = gt_relaxed - pred_relaxed
    extra = pred_relaxed - gt_relaxed

    return {
        "strict_precision": strict_precision,
        "strict_recall": strict_recall,
        "strict_f1": strict_f1,
        "relaxed_precision": relaxed_precision,
        "relaxed_recall": relaxed_recall,
        "relaxed_f1": relaxed_f1,
        "predicted_count": len(predicted),
        "ground_truth_count": len(ground_truth),
        "strict_tp": strict_tp,
        "relaxed_tp": relaxed_tp,
        "missed": sorted(str(m) for m in missed),
        "extra": sorted(str(e) for e in extra),
    }


def print_scores(
    mention_scores: dict,
    proposition_scores: dict,
    model: str = "",
    stats: dict | None = None,
) -> None:
    """Pretty-print benchmark results including model performance stats."""
    header = f"  Benchmark Results ({model})" if model else "  Benchmark Results"
    print(f"\n{'=' * 60}")
    print(header)
    print(f"{'=' * 60}")

    if stats:
        print("\n  Performance")
        if stats.get("duration_s"):
            print(f"    Total time:    {stats['duration_s']:.1f}s")
        if stats.get("chunk_count"):
            print(f"    Chunks:        {stats['chunk_count']}")
        if stats.get("duration_s") and stats.get("chunk_count"):
            per_chunk = stats["duration_s"] / stats["chunk_count"]
            print(f"    Per chunk:     {per_chunk:.1f}s")
        if stats.get("total_tokens"):
            print(f"    Total tokens:  {stats['total_tokens']:,}")
        if stats.get("tokens_per_sec"):
            print(f"    Tokens/sec:    {stats['tokens_per_sec']:.1f}")
        if stats.get("mention_retries") or stats.get("proposition_retries"):
            print(
                f"    Retries:       {stats.get('mention_retries', 0)} mention, "
                f"{stats.get('proposition_retries', 0)} proposition"
            )

    print(
        f"\n  Mentions ({mention_scores['predicted_count']} predicted, "
        f"{mention_scores['ground_truth_count']} ground truth)"
    )
    print(
        f"    Strict  — P: {mention_scores['strict_precision']:.3f}  "
        f"R: {mention_scores['strict_recall']:.3f}  F1: {mention_scores['strict_f1']:.3f}"
    )
    print(
        f"    Relaxed — P: {mention_scores['relaxed_precision']:.3f}  "
        f"R: {mention_scores['relaxed_recall']:.3f}  F1: {mention_scores['relaxed_f1']:.3f}"
    )
    print(f"    Type accuracy: {mention_scores['type_accuracy']:.3f}")
    print(f"    Span accuracy: {mention_scores['span_accuracy']:.3f}")
    if mention_scores["missed"]:
        print(f"    Missed: {mention_scores['missed'][:5]}")
    if mention_scores["extra"]:
        print(f"    Extra:  {mention_scores['extra'][:5]}")

    print(
        f"\n  Propositions ({proposition_scores['predicted_count']} predicted, "
        f"{proposition_scores['ground_truth_count']} ground truth)"
    )
    print(
        f"    Strict  — P: {proposition_scores['strict_precision']:.3f}  "
        f"R: {proposition_scores['strict_recall']:.3f}  F1: {proposition_scores['strict_f1']:.3f}"
    )
    print(
        f"    Relaxed — P: {proposition_scores['relaxed_precision']:.3f}  "
        f"R: {proposition_scores['relaxed_recall']:.3f}  F1: {proposition_scores['relaxed_f1']:.3f}"
    )
    if proposition_scores["missed"]:
        print(f"    Missed: {proposition_scores['missed'][:5]}")
    if proposition_scores["extra"]:
        print(f"    Extra:  {proposition_scores['extra'][:5]}")
    print(f"{'=' * 60}\n")


def print_comparison_table(results: list[dict]) -> None:
    """Print a comparison table across multiple models.

    Each result dict should have: model, mention_scores, proposition_scores, stats.
    """
    print(f"\n{'=' * 100}")
    print("  Multi-Model Benchmark Comparison")
    print(f"{'=' * 100}")
    print(
        f"\n  {'Model':<22} {'M-F1(s)':<9} {'M-F1(r)':<9} {'P-F1(s)':<9} {'P-F1(r)':<9} "
        f"{'Time(s)':<9} {'Tok/s':<8} {'Retries':<8} {'Mentions':<9}"
    )
    print(f"  {'-' * 92}")

    for r in results:
        m = r.get("mention_scores", {})
        p = r.get("proposition_scores", {})
        s = r.get("stats", {})
        name = r.get("model", "?")[:22]
        time_s = f"{s.get('duration_s', 0):.1f}" if s.get("duration_s") else "—"
        tok_s = f"{s.get('tokens_per_sec', 0):.0f}" if s.get("tokens_per_sec") else "—"
        retries = s.get("mention_retries", 0) + s.get("proposition_retries", 0)
        print(
            f"  {name:<22} {m.get('strict_f1', 0):<9.3f} {m.get('relaxed_f1', 0):<9.3f} "
            f"{p.get('strict_f1', 0):<9.3f} {p.get('relaxed_f1', 0):<9.3f} "
            f"{time_s:<9} {tok_s:<8} {retries:<8} {m.get('predicted_count', 0):<9}"
        )

    print(f"{'=' * 100}\n")


def print_benchmark_report(results: list[dict]) -> None:
    """Print a comprehensive benchmark report with entity matrix, SPO matrix, and stats.

    Args:
        results: list of dicts with keys: model, fixture, tags (optional)
    """
    if not results:
        return

    # ── Classify models by type ──────────────────────────────────────────
    encoders = []
    specialists = []
    llms = []
    for r in results:
        tags = r.get("tags", [])
        if "encoder" in tags:
            encoders.append(r)
        elif "extraction-specialist" in tags:
            specialists.append(r)
        else:
            llms.append(r)

    # ── Collect all unique entities and SPOs across all models ────────────
    all_entities: dict[str, set[str]] = {}  # entity_text -> set of models that found it
    all_entity_types: dict[str, dict[str, str]] = {}  # entity_text -> {model: type}
    all_spos: dict[str, set[str]] = {}  # "subj -> pred -> obj" -> set of models

    model_names = []
    for r in results:
        name = r["model"]
        model_names.append(name)
        ext = r["fixture"]

        for m in ext.get("mentions", []):
            text = m.get("text", "").strip()
            if not text:
                continue
            all_entities.setdefault(text, set()).add(name)
            all_entity_types.setdefault(text, {})[name] = m.get("mention_type", "?")

        for a in ext.get("assertions", []):
            subj = a.get("subject_text", a.get("subject", "")).strip()
            pred = a.get("predicate", "").strip()
            obj = a.get("object_text", a.get("object", "")).strip()
            if subj and pred and obj:
                spo_key = f"{subj} -> {pred} -> {obj}"
                all_spos.setdefault(spo_key, set()).add(name)

    # ── NER Entity Matrix ────────────────────────────────────────────────
    # Sort entities by how many models found them (consensus first)
    sorted_entities = sorted(all_entities.items(), key=lambda x: -len(x[1]))

    # Abbreviate model names for column headers
    short_names = []
    for n in model_names:
        short = n.replace("-", "").replace(".", "")[:8]
        short_names.append(short)

    print(f"\n{'=' * 100}")
    print("  NER Entity Matrix — which models found which entities")
    print(f"{'=' * 100}")
    print(f"\n  {'Entity':<30} {'Type':<8} {'#':>3}  ", end="")
    for sn in short_names:
        print(f"{sn:>9}", end="")
    print()
    print(f"  {'-' * (45 + 9 * len(short_names))}")

    for entity_text, found_by in sorted_entities:
        # Use the most common type across models
        types = all_entity_types.get(entity_text, {})
        type_counts: dict[str, int] = {}
        for t in types.values():
            type_counts[t] = type_counts.get(t, 0) + 1
        best_type = max(type_counts, key=type_counts.get) if type_counts else "?"

        display_text = entity_text[:29]
        print(f"  {display_text:<30} {best_type:<8} {len(found_by):>3}  ", end="")
        for name in model_names:
            if name in found_by:
                t = types.get(name, "?")
                marker = t[:3] if t != best_type else "  ✓"
                print(f"{marker:>9}", end="")
            else:
                print(f"{'·':>9}", end="")
        print()

    # ── SPO Proposition Matrix ───────────────────────────────────────────
    if all_spos:
        sorted_spos = sorted(all_spos.items(), key=lambda x: -len(x[1]))

        print(f"\n{'=' * 100}")
        print("  SPO Proposition Matrix — which models found which relationships")
        print(f"{'=' * 100}")
        print(f"\n  {'Subject -> Predicate -> Object':<55} {'#':>3}  ", end="")
        for sn in short_names:
            print(f"{sn:>9}", end="")
        print()
        print(f"  {'-' * (60 + 9 * len(short_names))}")

        for spo_key, found_by in sorted_spos[:30]:  # cap at 30 rows
            display = spo_key[:54]
            print(f"  {display:<55} {len(found_by):>3}  ", end="")
            for name in model_names:
                marker = "  ✓" if name in found_by else "  ·"
                print(f"{marker:>9}", end="")
            print()

    # ── Stats Table (grouped by type) ────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("  Performance Stats (grouped by extractor type)")
    print(f"{'=' * 100}")
    print(
        f"\n  {'Model':<22} {'Type':<12} {'Mentions':>8} {'Asserts':>8} "
        f"{'Time(s)':>8} {'Tok/s':>8} {'Retries':>8} {'Errors':>7}"
    )
    print(f"  {'-' * 88}")

    for group_name, group in [("ENCODERS", encoders), ("SPECIALISTS", specialists), ("LLMs", llms)]:
        if not group:
            continue
        for r in sorted(group, key=lambda x: x["fixture"].get("stats", {}).get("duration_s", 999)):
            s = r["fixture"].get("stats", {})
            retries = s.get("mention_retries", 0) + s.get("proposition_retries", 0)
            print(
                f"  {r['model']:<22} {group_name:<12} {s.get('mention_count', 0):>8} "
                f"{s.get('assertion_count', 0):>8} {s.get('duration_s', 0):>8.1f} "
                f"{s.get('tokens_per_sec', 0):>8.1f} {retries:>8} {s.get('errors', 0):>7}"
            )
        print(f"  {'-' * 88}")

    print(f"{'=' * 100}\n")
