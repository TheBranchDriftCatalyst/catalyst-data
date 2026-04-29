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


def _compute_scores(fixture: dict, gt_mentions: list, gt_propositions: list) -> dict:
    """Compute all metrics for one model against ground truth."""
    m_scores = score_mentions(fixture.get("mentions", []), gt_mentions)
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


def main():
    parser = argparse.ArgumentParser(description="Unified extraction benchmark harness")
    parser.add_argument("--regen", action="store_true", help="Clear and regenerate all fixtures")
    parser.add_argument("--audit-log", action="store_true", help="Save structured audit logs")
    parser.add_argument("--timeout", type=int, default=300, help="Per-model timeout (seconds)")
    parser.add_argument("--local-only", action="store_true", help="Skip cloud models")
    parser.add_argument("--generate-ground-truth", action="store_true", help="Generate ground truth from best model")
    parser.add_argument("--ground-truth-model", type=str, default="gpt-4o", help="Model for ground truth generation")
    args = parser.parse_args()

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

    total_time = time.monotonic() - t0

    # Load ground truth and compute scores
    gt = _load_fixture("ground_truth_media_ingest")
    if gt:
        gt_mentions = []
        gt_propositions = []
        for chunk in gt["chunks"]:
            gt_mentions.extend(chunk["mentions"])
            gt_propositions.extend(chunk["propositions"])

        for r in results:
            r["scores"] = _compute_scores(r["fixture"], gt_mentions, gt_propositions)

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
