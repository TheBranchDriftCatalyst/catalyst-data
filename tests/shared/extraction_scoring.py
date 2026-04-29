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
    chunk_texts: dict[str, str] | None = None,
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
    for m in predicted:
        s, e = m.get("span_start"), m.get("span_end")
        if s is None or e is None:
            continue
        # Resolve source text: per-chunk dict takes priority, then single source_text
        src = None
        if chunk_texts:
            src = chunk_texts.get(m.get("chunk_id", ""))
        elif source_text:
            src = source_text
        if src and 0 <= s < e <= len(src):
            span_total += 1
            if src[s:e] == m.get("text", ""):
                span_correct += 1
    # None when source text unavailable (can't compute), 0.0-1.0 otherwise
    span_accuracy: float | None = span_correct / span_total if span_total else None

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

    def _get_subj(p: dict) -> str:
        return p.get("subject") or p.get("subject_text") or ""

    def _get_obj(p: dict) -> str:
        return p.get("object") or p.get("object_text") or ""

    def _triple_strict(p: dict) -> tuple:
        return (
            _normalize(_get_subj(p)),
            _normalize(p.get("predicate", "")),
            _normalize(_get_obj(p)),
        )

    def _triple_relaxed(p: dict) -> tuple:
        return (
            _normalize(_get_subj(p)),
            _normalize(_get_obj(p)),
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
    """Print a concise benchmark summary to the terminal.

    Full entity/SPO matrices and detailed charts are in the viewer SPA —
    the CLI output focuses on the stats table, pipeline health, and scores.
    """
    if not results:
        return

    # ── Classify models ─────────────────────────────────────────────────
    groups: dict[str, list[dict]] = {"encoder": [], "specialist": [], "llm": []}
    for r in results:
        tags = r.get("tags", [])
        if "encoder" in tags:
            groups["encoder"].append(r)
        elif "extraction-specialist" in tags:
            groups["specialist"].append(r)
        else:
            groups["llm"].append(r)

    # ── Quick counts ────────────────────────────────────────────────────
    total_mentions = sum(r["fixture"].get("stats", {}).get("mention_count", 0) for r in results)
    total_asserts = sum(r["fixture"].get("stats", {}).get("assertion_count", 0) for r in results)
    unique_entities: set[str] = set()
    for r in results:
        for m in r["fixture"].get("mentions", []):
            t = m.get("text", "").strip()
            if t:
                unique_entities.add(t.lower())

    print(
        f"\n  {len(results)} models | {len(unique_entities)} unique entities | {total_mentions} mentions | {total_asserts} assertions"
    )

    # ── Stats Table ─────────────────────────────────────────────────────
    print(f"\n  {'Model':<22} {'Type':<10} {'NER':>5} {'SPO':>5} {'Time':>7} {'Tok/s':>6} {'Retry':>5} {'Err':>4}")
    print(f"  {'-' * 70}")

    group_labels = {"encoder": "ENC", "specialist": "SPEC", "llm": "LLM"}
    for gkey in ["encoder", "specialist", "llm"]:
        for r in sorted(groups[gkey], key=lambda x: x["fixture"].get("stats", {}).get("duration_s", 999)):
            s = r["fixture"].get("stats", {})
            retries = s.get("mention_retries", 0) + s.get("proposition_retries", 0)
            print(
                f"  {r['model']:<22} {group_labels[gkey]:<10} "
                f"{s.get('mention_count', 0):>5} {s.get('assertion_count', 0):>5} "
                f"{s.get('duration_s', 0):>6.1f}s {s.get('tokens_per_sec', 0):>6.0f} "
                f"{retries:>5} {s.get('errors', 0):>4}"
            )

    # ── Pipeline error summary (one line) ───────────────────────────────
    all_error_codes: dict[str, int] = {}
    for r in results:
        pipeline = r["fixture"].get("stats", {}).get("pipeline", {})
        for stage_info in pipeline.values():
            for code, count in (stage_info.get("error_codes") or {}).items():
                all_error_codes[code] = all_error_codes.get(code, 0) + count
    if all_error_codes:
        errs = ", ".join(f"{code}: {count}" for code, count in sorted(all_error_codes.items(), key=lambda x: -x[1]))
        print(f"\n  MCP errors: {errs}")

    # ── F1 scores (if ground truth available) ───────────────────────────
    scored = [r for r in results if r.get("scores")]
    if scored:
        print(f"\n  {'Model':<22} {'Strict F1':>10} {'Relax F1':>10} {'SPO F1':>10} {'Span Acc':>10}")
        print(f"  {'-' * 62}")
        for r in sorted(scored, key=lambda x: -(x["scores"].get("mention_strict_f1", 0))):
            sc = r["scores"]
            span = sc.get("mention_span_accuracy", 0)
            print(
                f"  {r['model']:<22} "
                f"{sc['mention_strict_f1'] * 100:>9.1f}% "
                f"{sc['mention_relaxed_f1'] * 100:>9.1f}% "
                f"{sc.get('proposition_relaxed_f1', 0) * 100:>9.1f}% "
                f"{span * 100:>9.1f}%"
            )

    print("\n  Full report: open the benchmark viewer SPA for entity/SPO matrices and charts.")
    print()
