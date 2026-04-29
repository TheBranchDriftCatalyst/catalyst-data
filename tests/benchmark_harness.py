"""Unified benchmark harness — single entry point for extraction benchmarking.

Wraps the existing test infrastructure into a clean CLI that:
1. Runs extraction across all configured models
2. Computes F1/precision/recall against ground truth (if available)
3. Generates the benchmark report JSON for the viewer SPA
4. Optionally saves structured audit logs
5. Reports latency, throughput, hallucination rate, quality/speed ratio

Usage:
    # Full benchmark with all flags:
    python tests/benchmark_harness.py --regen --audit-log --timeout 600

    # Quick run (cached fixtures):
    python tests/benchmark_harness.py

    # With exgraph v2:
    EXGRAPH_ENABLED=true python tests/benchmark_harness.py --regen

    # Generate ground truth from best model:
    python tests/benchmark_harness.py --generate-ground-truth --ground-truth-model gpt-4o
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.benchmark_config import ALL_MODELS, CLOUD_MODELS, LOCAL_MODELS, ModelConfig
from tests.shared.extraction_scoring import (
    print_benchmark_report,
    score_mentions,
    score_propositions,
)

TEST_OUTPUT = Path(os.environ.get("TEST_OUTPUT_ROOT", str(ROOT / ".test-output"))) / "media-ingest"
FIXTURES = TEST_OUTPUT / "fixtures"
AUDIT_DIR = TEST_OUTPUT / "audit-logs"
REPORT_PATH = TEST_OUTPUT / "benchmark-report.json"


def _load_fixture(name: str) -> dict | None:
    f = FIXTURES / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else None


def _run_model(cfg: ModelConfig, timeout: int, save_audit: bool) -> dict | None:
    """Run extraction for one model via subprocess."""
    is_cloud = "cloud" in cfg.tags
    if is_cloud:
        api_key = cfg.api_key or os.environ.get("LLM_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        base_url = cfg.base_url
    else:
        api_key = "unused"
        base_url = cfg.base_url or "http://localhost:11434/v1"

    env = {
        **os.environ,
        "LLM_MODEL": cfg.model,
        "LLM_BASE_URL": base_url,
        "LLM_API_KEY": api_key,
        "OPENAI_API_KEY": api_key,
        "LLM_STRUCTURED_METHOD": cfg.structured_method,
        "LLM_MAX_TOKENS": str(cfg.max_tokens),
        "LLM_TIMEOUT": str(timeout),
        "SAVE_AUDIT_LOG": "true" if save_audit else "",
        "PROMPT_REGISTRY_DIR": str(ROOT / "k8s" / "shared" / "prompts"),
        "PYTHONPATH": str(ROOT),
    }

    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_pipeline_integration.py",
                "-k",
                "extraction_produces_mentions",
                "-v",
                "-s",
                "--no-header",
                "--tb=short",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ROOT),
        )
        return _load_fixture(f"extraction_{cfg.model}")
    except subprocess.TimeoutExpired:
        return None


def _compute_scores(
    fixture: dict,
    gt_mentions: list,
    gt_propositions: list,
    chunk_texts: dict[str, str] | None = None,
) -> dict:
    """Compute all metrics for one model against ground truth."""
    m_scores = score_mentions(fixture.get("mentions", []), gt_mentions, chunk_texts=chunk_texts)
    p_scores = score_propositions(fixture.get("assertions", []), gt_propositions)
    stats = fixture.get("stats", {})

    duration = stats.get("duration_s", 0)
    chunk_count = stats.get("chunk_count", 1)

    return {
        # F1 / Precision / Recall
        "mention_strict_precision": m_scores["strict_precision"],
        "mention_strict_recall": m_scores["strict_recall"],
        "mention_strict_f1": m_scores["strict_f1"],
        "mention_relaxed_precision": m_scores["relaxed_precision"],
        "mention_relaxed_recall": m_scores["relaxed_recall"],
        "mention_relaxed_f1": m_scores["relaxed_f1"],
        "mention_type_accuracy": m_scores["type_accuracy"],
        "mention_span_accuracy": m_scores["span_accuracy"],
        "proposition_strict_precision": p_scores["strict_precision"],
        "proposition_strict_recall": p_scores["strict_recall"],
        "proposition_strict_f1": p_scores["strict_f1"],
        "proposition_relaxed_precision": p_scores["relaxed_precision"],
        "proposition_relaxed_recall": p_scores["relaxed_recall"],
        "proposition_relaxed_f1": p_scores["relaxed_f1"],
        # Hallucination
        "hallucination_rate": round(1.0 - m_scores["span_accuracy"], 3),
        # Efficiency
        "quality_speed_ratio": round(m_scores["strict_f1"] / max(duration, 0.1), 4),
        "per_chunk_latency": round(duration / max(chunk_count, 1), 2),
        "tokens_per_sec": stats.get("tokens_per_sec", 0),
    }


def _run_ensemble_gt():
    """Generate ensemble ground truth from cached extraction fixtures."""
    from tests.test_extraction_benchmark import generate_ensemble_ground_truth

    print(f"\n{'=' * 70}")
    print("  Ensemble Ground Truth Generation")
    print(f"{'=' * 70}")

    gt_path = FIXTURES / "ground_truth_media_ingest.json"
    if gt_path.exists():
        existing = json.loads(gt_path.read_text())
        if existing.get("manually_reviewed"):
            print("\n  Skipped: ground truth has been manually reviewed.")
            print(f"{'=' * 70}")
            return

    ground_truth = generate_ensemble_ground_truth()
    if ground_truth is None:
        print("\n  Failed: not enough model fixtures for ensemble (need >= 2).")
        print(f"{'=' * 70}")
        return

    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / "ground_truth_media_ingest.json").write_text(json.dumps(ground_truth, indent=2, default=str))

    config = ground_truth["ensemble_config"]
    print("\n  Ensemble ground truth saved:")
    print(f"    NER models ({len(config['ner_models'])}): {', '.join(config['ner_models'])}")
    print(f"    SPO models ({len(config['spo_models'])}): {', '.join(config['spo_models'])}")
    print(f"    Threshold: NER >= {config['ner_threshold']}, SPO >= {config['spo_threshold']}")
    print(f"    Mentions: {ground_truth['total_mentions']} | Propositions: {ground_truth['total_propositions']}")
    print(f"    File: {gt_path}")
    print(f"{'=' * 70}")


def _interactive_prompt() -> argparse.Namespace:
    """Interactive mode — ask user what to run when no flags provided."""
    current_pipeline = "exgraph v2" if os.environ.get("EXGRAPH_ENABLED") == "true" else "v1 (legacy)"

    print(f"\n{'=' * 70}")
    print("  Extraction Benchmark Harness — Interactive Mode")
    print(f"  Current pipeline: {current_pipeline}")
    print(f"{'=' * 70}\n")

    options = [
        ("full", "Full methodology (run models → ensemble GT → score → report)"),
        ("regen", "Regenerate all extraction fixtures"),
        ("ensemble-gt", "Generate ensemble ground truth only"),
        ("audit-log", "Save detailed audit logs"),
        ("local-only", "Skip cloud models (no API key needed)"),
        ("exgraph", "Use exgraph v2 pipeline (instead of v1 legacy)"),
        ("compare", "Compare v1 vs v2 (runs both, side-by-side report)"),
    ]

    for i, (_, desc) in enumerate(options, 1):
        marker = " *" if (i == 6 and os.environ.get("EXGRAPH_ENABLED") == "true") else ""
        print(f"  [{i}] {desc}{marker}")
    print("  [Enter] Default run (use cached fixtures, score, report)")
    print()

    raw = input("  Select options (comma-separated, e.g. 1,4): ").strip()

    args = argparse.Namespace(
        regen=False,
        audit_log=False,
        timeout=300,
        local_only=False,
        generate_ground_truth=False,
        ground_truth_model="gpt-4o",
        ensemble_gt=False,
        full=False,
        compare=False,
        exgraph=False,
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
                args.audit_log = True
            elif choice == "5":
                args.local_only = True
            elif choice == "6":
                os.environ["EXGRAPH_ENABLED"] = "true"
            elif choice == "7":
                args.compare = True

    timeout_raw = input(f"  Timeout per model [{args.timeout}s]: ").strip()
    if timeout_raw.isdigit():
        args.timeout = int(timeout_raw)

    print()
    return args


def _run_comparison(args):
    """Run v1 and v2 pipelines and print side-by-side F1 comparison."""
    import shutil

    v1_dir = TEST_OUTPUT / "compare-v1"
    v2_dir = TEST_OUTPUT / "compare-v2"

    for version, output_dir, env_val in [("v1 (legacy)", v1_dir, "false"), ("v2 (exgraph)", v2_dir, "true")]:
        print(f"\n{'=' * 70}")
        print(f"  Running {version} pipeline...")
        print(f"{'=' * 70}")

        # Point fixtures to a version-specific directory
        env = {
            **os.environ,
            "EXGRAPH_ENABLED": env_val,
            "TEST_OUTPUT_ROOT": str(output_dir),
        }

        # Copy shared fixtures (chunks + ground truth) so both runs use the same baseline
        dst_fixtures = output_dir / "media-ingest" / "fixtures"
        dst_fixtures.mkdir(parents=True, exist_ok=True)
        for shared_file in ["chunks.json", "ground_truth_media_ingest.json"]:
            src = FIXTURES / shared_file
            if src.exists():
                shutil.copy2(src, dst_fixtures / shared_file)

        subprocess.run(
            [sys.executable, __file__, "--regen", "--timeout", str(args.timeout)]
            + (["--local-only"] if args.local_only else [])
            + (["--audit-log"] if args.audit_log else []),
            env=env,
            timeout=args.timeout * 20,
        )

    # Load both reports and compare
    v1_report = v1_dir / "media-ingest" / "benchmark-report.json"
    v2_report = v2_dir / "media-ingest" / "benchmark-report.json"

    if not v1_report.exists() or not v2_report.exists():
        print("\n  ERROR: one or both runs failed to produce a report")
        return

    v1 = json.loads(v1_report.read_text())
    v2 = json.loads(v2_report.read_text())

    v1_models = {m["name"]: m for m in v1["models"]}
    v2_models = {m["name"]: m for m in v2["models"]}
    all_names = sorted(set(v1_models) | set(v2_models))

    print(f"\n{'=' * 90}")
    print("  v1 vs v2 Pipeline Comparison")
    print(f"{'=' * 90}")
    print(
        f"\n  {'Model':<22} {'v1 Strict F1':>14} {'v2 Strict F1':>14} {'Delta':>10} {'v1 SPAN_ERR':>12} {'v2 SPAN_ERR':>12}"
    )
    print(f"  {'-' * 84}")

    for name in all_names:
        m1 = v1_models.get(name)
        m2 = v2_models.get(name)
        f1_v1 = m1["scores"]["mention_strict_f1"] * 100 if m1 and m1.get("scores") else None
        f1_v2 = m2["scores"]["mention_strict_f1"] * 100 if m2 and m2.get("scores") else None

        # Count SPAN_MISMATCH from pipeline breakdown
        def _span_errors(m):
            if not m:
                return "—"
            pipeline = m.get("pipeline", {})
            total = 0
            for stage_info in pipeline.values():
                for code, count in (stage_info.get("error_codes", {}) or {}).items():
                    if "SPAN" in code:
                        total += count
            return str(total) if total else "0"

        s1 = f"{f1_v1:.1f}%" if f1_v1 is not None else "—"
        s2 = f"{f1_v2:.1f}%" if f1_v2 is not None else "—"
        delta = ""
        if f1_v1 is not None and f1_v2 is not None:
            d = f1_v2 - f1_v1
            delta = f"{'+' if d >= 0 else ''}{d:.1f}%"

        print(f"  {name:<22} {s1:>14} {s2:>14} {delta:>10} {_span_errors(m1):>12} {_span_errors(m2):>12}")

    print("\n  Reports saved:")
    print(f"    v1: {v1_report}")
    print(f"    v2: {v2_report}")
    print(f"{'=' * 90}")


def main():
    parser = argparse.ArgumentParser(description="Unified extraction benchmark harness")
    parser.add_argument("--regen", action="store_true", help="Clear and regenerate all fixtures")
    parser.add_argument("--audit-log", action="store_true", help="Save structured audit logs")
    parser.add_argument("--timeout", type=int, default=300, help="Per-model timeout (seconds)")
    parser.add_argument("--local-only", action="store_true", help="Skip cloud models")
    parser.add_argument("--generate-ground-truth", action="store_true", help="Generate ground truth from best model")
    parser.add_argument("--ground-truth-model", type=str, default="gpt-4o", help="Model for ground truth generation")
    parser.add_argument("--ensemble-gt", action="store_true", help="Generate ground truth via multi-model consensus")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full methodology: run all models → ensemble ground truth → score → report",
    )
    parser.add_argument("--exgraph", action="store_true", help="Use exgraph v2 pipeline")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare v1 vs v2: run both pipelines and print side-by-side F1 scores",
    )
    args = parser.parse_args()

    # Interactive mode when no flags given
    if len(sys.argv) == 1:
        args = _interactive_prompt()

    # --exgraph sets the env var
    if args.exgraph:
        os.environ["EXGRAPH_ENABLED"] = "true"

    # --full implies --ensemble-gt (run everything in one shot)
    if args.full:
        args.ensemble_gt = True

    # ── Compare mode: run v1 then v2, side-by-side ────────────────────
    if args.compare:
        _run_comparison(args)
        return

    # ── Ensemble-only mode (no model runs) ──────────────────────────────
    if args.ensemble_gt and not args.full:
        _run_ensemble_gt()
        return

    print(f"\n{'=' * 70}")
    print("  Extraction Benchmark Harness")
    print(f"  Models: {len(ALL_MODELS)} ({len(LOCAL_MODELS)} local + {len(CLOUD_MODELS)} cloud)")
    print(f"  Timeout: {args.timeout}s | Regen: {args.regen} | Audit: {args.audit_log}")
    print(f"  Pipeline: {'exgraph v2' if os.environ.get('EXGRAPH_ENABLED') == 'true' else 'v1 (legacy)'}")
    print(f"{'=' * 70}\n")

    if args.regen:
        for f in FIXTURES.glob("extraction_*.json"):
            f.unlink()
        print("  Cleared extraction fixtures\n")

    if args.audit_log:
        import shutil

        if AUDIT_DIR.exists():
            shutil.rmtree(AUDIT_DIR)
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    models = LOCAL_MODELS if args.local_only else ALL_MODELS
    results = []
    t0 = time.monotonic()

    for cfg in models:
        # Skip cloud if no key
        if "cloud" in cfg.tags:
            api_key = cfg.api_key or os.environ.get("LLM_API_KEY", "")
            if not api_key:
                print(f"  SKIP {cfg.name}: no API key")
                continue

        # Check cache
        cached = _load_fixture(f"extraction_{cfg.model}")
        if cached and not args.regen:
            s = cached.get("stats", {})
            print(f"  CACHED {cfg.name}: {s.get('mention_count', '?')} mentions, {s.get('duration_s', '?')}s")
            results.append({"model": cfg.name, "fixture": cached, "tags": cfg.tags})
            continue

        # Check endpoint
        if cfg.base_url and "cloud" not in cfg.tags:
            import urllib.request

            try:
                urllib.request.urlopen(urllib.request.Request(f"{cfg.base_url}/models", method="GET"), timeout=3)
            except Exception:
                print(f"  SKIP {cfg.name}: endpoint not reachable")
                continue

        print(f"  RUNNING {cfg.name}...", end=" ", flush=True)
        fixture = _run_model(cfg, args.timeout, args.audit_log)
        if fixture:
            s = fixture.get("stats", {})
            print(f"OK ({s.get('mention_count', 0)} mentions, {s.get('duration_s', 0)}s)")
            results.append({"model": cfg.name, "fixture": fixture, "tags": cfg.tags})

            # Save audit log
            if args.audit_log:
                audit_events = s.get("audit_events", [])
                if audit_events:
                    safe = cfg.name.replace("/", "_").replace(":", "_")
                    with open(AUDIT_DIR / f"{safe}.json", "w") as f:
                        json.dump(
                            {
                                "model": cfg.model,
                                "name": cfg.name,
                                "tags": cfg.tags,
                                "stats": {k: v for k, v in s.items() if k != "audit_events"},
                                "audit_events": audit_events,
                                "event_count": len(audit_events),
                            },
                            f,
                            indent=2,
                            default=str,
                        )
        else:
            print("TIMEOUT/FAIL")

        # Unload local model from Ollama VRAM to free memory for next model
        if "cloud" not in cfg.tags and "encoder" not in cfg.tags:
            subprocess.run(["ollama", "stop", cfg.model], capture_output=True, timeout=10)

    total_time = time.monotonic() - t0

    # Generate ensemble ground truth if --full
    if args.ensemble_gt:
        _run_ensemble_gt()

    # Load ground truth and compute scores
    gt = _load_fixture("ground_truth_media_ingest")
    if gt:
        gt_mentions = []
        gt_propositions = []
        for chunk in gt["chunks"]:
            gt_mentions.extend(chunk["mentions"])
            gt_propositions.extend(chunk["propositions"])

        # Load chunk source texts for span accuracy
        chunks_data = _load_fixture("chunks")
        chunk_texts = {c["chunk_id"]: c["text"] for c in chunks_data} if chunks_data else None

        for r in results:
            r["scores"] = _compute_scores(r["fixture"], gt_mentions, gt_propositions, chunk_texts)

    # Build and save report
    from tests.test_extraction_benchmark import _build_report_json

    report = _build_report_json(results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Print summary
    print(f"\n{'=' * 70}")
    print(f"  Complete: {len(results)} models in {total_time:.0f}s")
    print(f"  Report: {REPORT_PATH}")
    if gt:
        print(
            f"  Ground truth: {gt['reference_model']} ({'reviewed' if gt.get('manually_reviewed') else 'unreviewed'})"
        )
    else:
        print("  Ground truth: not available (run with --generate-ground-truth)")
    if args.audit_log:
        print(f"  Audit logs: {AUDIT_DIR}")
    print(f"{'=' * 70}")

    # Print the full report
    if results:
        print_benchmark_report(results)


if __name__ == "__main__":
    main()
