"""Unified benchmark harness -- single entry point for extraction benchmarking.

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

from tests.benchmark_config import ALL_MODELS, LOCAL_MODELS, BenchmarkConfig, ModelConfig
from tests.shared.extraction_scoring import (
    compute_model_scores,
    print_benchmark_report,
)
from tests.shared.ground_truth import generate_ensemble_ground_truth
from tests.shared.medallion import load_chunks
from tests.shared.report import build_report_json
from tests.shared.store import BenchmarkStore


def _run_model(
    cfg: ModelConfig,
    timeout: int,
    save_audit: bool,
    store: BenchmarkStore,
    all_videos: bool = False,
) -> dict | None:
    """Run extraction for one model via subprocess.

    When ``all_videos=False`` (default): pytest fixture chain on tests/demo_video.mp4 (single).
    When ``all_videos=True``: scripts/bench_extract_per_video.py iterates the manifest.
    Returns the latest run's aggregate extraction record, loadable via ``store.load_fixture``.
    """
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
        "LLM_CONTEXT_WINDOW": str(cfg.context_window),
        "LLM_TIMEOUT": str(timeout),
        "SAVE_AUDIT_LOG": "true" if save_audit else "",
        "PROMPT_REGISTRY_DIR": str(ROOT / "k8s" / "shared" / "prompts"),
        "PYTHONPATH": str(ROOT),
    }

    if all_videos:
        cmd = [sys.executable, str(ROOT / "scripts" / "bench_extract_per_video.py")]
    else:
        cmd = [
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
        ]

    try:
        subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ROOT),
        )
        # Both paths write extraction_<model>.json — single-video to legacy fixture, multi-video
        # to runs/<run>/extractions/extraction_<model>.json (aggregate). load_fixture finds either.
        run = store.load_run("latest")
        if all_videos and run is not None:
            cached = run.load_extraction(cfg.model)
            if cached:
                return cached
        return store.load_fixture(f"extraction_{cfg.model}")
    except subprocess.TimeoutExpired:
        return None


def _run_ensemble_gt(
    store: BenchmarkStore,
    ner_models: list[str] | None = None,
    spo_models: list[str] | None = None,
):
    """Generate ensemble ground truth from cached extraction artifacts."""
    print(f"\n{'=' * 70}")
    print("  Ensemble Ground Truth Generation")
    print(f"{'=' * 70}")

    existing = store.load_ground_truth("active")
    if existing and existing.get("manually_reviewed"):
        print("\n  Skipped: ground truth has been manually reviewed.")
        print(f"{'=' * 70}")
        return

    ground_truth = generate_ensemble_ground_truth(store, ner_models=ner_models, spo_models=spo_models)
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
    current_pipeline = "exgraph v2" if os.environ.get("EXGRAPH_ENABLED") == "true" else "v1 (legacy)"

    print(f"\n{'=' * 70}")
    print("  Extraction Benchmark Harness -- Interactive Mode")
    print(f"  Current pipeline: {current_pipeline}")
    print(f"{'=' * 70}\n")

    options = [
        ("full", "Full methodology (run models -> ensemble GT -> score -> report)"),
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
        generate_gt=False,
        gt_model="gpt-4o",
        ensemble_gt=False,
        full=False,
        compare=False,
        exgraph=False,
        models=None,
        ner_models=None,
        spo_models=None,
        label=None,
        list_gt=False,
        list_runs=False,
        use_gt=None,
        clean=False,
        view=False,
        score=False,
        report=False,
        compare_runs=None,
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
    store = BenchmarkStore()

    for version, label, env_val in [("v1 (legacy)", "legacy-v1", "false"), ("v2 (exgraph)", "exgraph-v2", "true")]:
        print(f"\n{'=' * 70}")
        print(f"  Running {version} pipeline...")
        print(f"{'=' * 70}")

        store.create_run(label=label)

        env = {
            **os.environ,
            "EXGRAPH_ENABLED": env_val,
            # Still use the main TEST_OUTPUT_ROOT so test_pipeline_integration
            # saves fixtures to the standard location
            "TEST_OUTPUT_ROOT": str(store.root.parent),
        }

        subprocess.run(
            [sys.executable, __file__, "--regen", "--timeout", str(args.timeout)]
            + (["--local-only"] if args.local_only else [])
            + (["--audit-log"] if args.audit_log else []),
            env=env,
            timeout=args.timeout * 20,
        )

    # Load reports from the two most recent runs
    runs = store.list_runs()
    if len(runs) < 2:
        print("\n  ERROR: need at least 2 runs for comparison")
        return

    v1_run = store.load_run(runs[-2])
    v2_run = store.load_run(runs[-1])

    if not v1_run or not v2_run:
        print("\n  ERROR: could not load comparison runs")
        return

    v1 = v1_run.load_report()
    v2 = v2_run.load_report()

    if not v1 or not v2:
        # Fallback: try old compare layout
        v1_path = store.root / "compare-v1" / "media-ingest" / "benchmark-report.json"
        v2_path = store.root / "compare-v2" / "media-ingest" / "benchmark-report.json"
        if v1_path.exists() and v2_path.exists():
            v1 = json.loads(v1_path.read_text())
            v2 = json.loads(v2_path.read_text())
        else:
            print("\n  ERROR: one or both runs failed to produce a report")
            return

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

        def _span_errors(m):
            if not m:
                return "---"
            pipeline = m.get("pipeline", {})
            total = 0
            for stage_info in pipeline.values():
                for code, count in (stage_info.get("error_codes", {}) or {}).items():
                    if "SPAN" in code:
                        total += count
            return str(total) if total else "0"

        s1 = f"{f1_v1:.1f}%" if f1_v1 is not None else "---"
        s2 = f"{f1_v2:.1f}%" if f1_v2 is not None else "---"
        delta = ""
        if f1_v1 is not None and f1_v2 is not None:
            d = f1_v2 - f1_v1
            delta = f"{'+' if d >= 0 else ''}{d:.1f}%"

        print(f"  {name:<22} {s1:>14} {s2:>14} {delta:>10} {_span_errors(m1):>12} {_span_errors(m2):>12}")

    print(f"\n{'=' * 90}")


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
        print(f"  Directory: {store.ground_truth_dir}")
        return
    print(f"\n  Available ground truths ({store.ground_truth_dir}):")
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


def _list_runs(store: BenchmarkStore) -> None:
    """List available benchmark runs."""
    runs = store.list_runs()
    if not runs:
        print("  No benchmark runs found.")
        print(f"  Directory: {store.runs_dir}")
        return
    print(f"\n  Available runs ({store.runs_dir}):")
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
    # Show latest symlink target
    latest = store.runs_dir / "latest"
    if latest.is_symlink():
        print(f"\n    latest -> {latest.resolve().name}")


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
    print(f"\n  Report updated: {store.root / 'benchmark-report.json'}")


def main():
    parser = argparse.ArgumentParser(
        description="Unified extraction benchmark harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python tests/benchmark_harness.py                       # interactive mode
  python tests/benchmark_harness.py --full --exgraph      # full methodology
  python tests/benchmark_harness.py --models mistral-7b,gliner-large
  python tests/benchmark_harness.py --ensemble-gt         # GT only
  python tests/benchmark_harness.py --list-gt             # show ground truths
  python tests/benchmark_harness.py --list-runs           # show runs
  python tests/benchmark_harness.py --score               # re-score latest
  python tests/benchmark_harness.py --clean               # clean artifacts
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
    action.add_argument("--compare", action="store_true", help="Compare v1 vs v2 pipelines side-by-side")
    action.add_argument("--score", action="store_true", help="Re-score latest run against active GT (no model runs)")
    action.add_argument("--report", action="store_true", help="Rebuild report JSON from latest run (no model runs)")
    action.add_argument("--list-gt", action="store_true", help="List available ground truth files")
    action.add_argument("--use-gt", type=str, metavar="NAME", help="Set active ground truth by name")
    action.add_argument("--list-runs", action="store_true", help="List timestamped benchmark runs")
    action.add_argument("--clean", action="store_true", help="Clean cached artifacts (keep true fixtures)")
    action.add_argument("--view", action="store_true", help="Start the benchmark viewer SPA")

    # ── Configuration flags ──────────────────────────────────────────
    config = parser.add_argument_group("configuration")
    config.add_argument("--regen", action="store_true", help="Clear and regenerate all extraction artifacts")
    config.add_argument("--audit-log", action="store_true", help="Save structured audit logs")
    config.add_argument("--timeout", type=int, default=300, help="Per-model timeout in seconds (default: 300)")
    config.add_argument("--local-only", action="store_true", help="Skip cloud models")
    config.add_argument("--exgraph", action="store_true", help="Use exgraph v2 pipeline")
    config.add_argument(
        "--all-videos",
        action="store_true",
        help="Run each model across every video in packages/media-ingest/tests/fixtures/audio_manifest.yaml "
        "(multi-video mode). Uses cached transcription/diarization, runs chunker + extraction per video. "
        "Default: single-video pytest fixture path.",
    )
    config.add_argument("--label", type=str, metavar="NAME", help="Label for this run (used in runs/ dir name)")
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
        "--models",
        type=str,
        metavar="LIST",
        help="Run only specific models (comma-separated names from benchmark_config)",
    )
    config.add_argument(
        "--ner-models",
        type=str,
        metavar="LIST",
        help="Override ensemble NER panel (comma-separated model names)",
    )
    config.add_argument(
        "--spo-models",
        type=str,
        metavar="LIST",
        help="Override ensemble SPO panel (comma-separated model names)",
    )

    # ── Deprecated (kept for backward compat) ────────────────────────
    config.add_argument("--generate-ground-truth", action="store_true", help=argparse.SUPPRESS)
    config.add_argument("--ground-truth-model", type=str, default="gpt-4o", help=argparse.SUPPRESS)
    config.add_argument("--compare-runs", type=str, metavar="RUN1,RUN2", help=argparse.SUPPRESS)

    args = parser.parse_args()

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

    # --exgraph sets the env var
    if args.exgraph:
        os.environ["EXGRAPH_ENABLED"] = "true"

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

    if getattr(args, "compare_runs", None):
        raise NotImplementedError(
            "--compare-runs is not yet implemented. "
            "Use --compare for v1-vs-v2 pipeline comparison, "
            "or manually compare reports in .test-output/media-ingest/runs/"
        )

    # ── Compare mode: run v1 then v2, side-by-side ────────────────────
    if args.compare:
        _run_comparison(args)
        return

    # ── Ensemble-only mode (no model runs) ──────────────────────────────
    if args.ensemble_gt and not args.full:
        ner_override = args.ner_models.split(",") if getattr(args, "ner_models", None) else None
        spo_override = args.spo_models.split(",") if getattr(args, "spo_models", None) else None
        # Temporarily patch the generation call
        _run_ensemble_gt(store, ner_models=ner_override, spo_models=spo_override)
        return

    # Determine which models to run
    if getattr(args, "models", None):
        from tests.benchmark_config import get_model_by_name

        requested = [n.strip() for n in args.models.split(",")]
        models = []
        for name in requested:
            cfg = get_model_by_name(name)
            if cfg:
                models.append(cfg)
            else:
                print(f"  WARNING: model '{name}' not found in benchmark_config.py")
    elif args.local_only:
        models = LOCAL_MODELS
    else:
        models = ALL_MODELS

    chunk_size_str = f"{args.chunk_size} tokens" if getattr(args, "chunk_size", None) else "default"
    pipeline_label_str = "exgraph v2" if os.environ.get("EXGRAPH_ENABLED") == "true" else "v1 (legacy)"
    pipeline_label = getattr(args, "label", None) or (
        "exgraph-v2" if os.environ.get("EXGRAPH_ENABLED") == "true" else "legacy-v1"
    )

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
    cached_audio_doc_ids = (
        sorted(d.name for d in store.pipeline_cache_dir.iterdir() if d.is_dir() and d.name != "model_cache")
        if store.pipeline_cache_dir.exists()
        else []
    )
    benchmark_chunks = load_chunks()

    n_local = sum(1 for m in models if "cloud" not in m.tags)
    n_cloud = sum(1 for m in models if "cloud" in m.tags)
    n_encoder = sum(1 for m in models if "encoder" in m.tags)

    # Honest accounting: in single-video mode we'll only process ONE video
    # (the integration-test demo). Showing "7 manifest videos" here misleads
    # users who haven't passed --all-videos. The corpus line scopes to what
    # will actually run.
    if args.all_videos:
        scope_label = "multi-video (--all-videos · media-ingest)"
        active_video_label = f"{len(manifest_videos)} manifest video(s)"
        active_chunks = len(benchmark_chunks)  # merged across all manifest docs
    else:
        scope_label = "single-video (demo_video)"
        active_video_label = "1 demo video (demo_video.mp4)"
        # Single-video scope: count chunks that match the demo doc; the
        # integration test fixture path also feeds a `:full` legacy chunk in
        # some configurations, so we count both demo doc-id forms.
        active_chunks = sum(
            1 for c in benchmark_chunks if c.get("document_id") in {"demo-video", "test-demo-video", ""}
        )

    print(f"\n{'═' * 78}")
    print("  catalyst-data │ extraction benchmark harness")
    print(f"  pipeline = {pipeline_label_str:<20} run-label = {pipeline_label or '(auto)'}")
    print(f"{'─' * 78}")
    print(f"  models      {len(models):<3} ({n_encoder} encoder · {n_local - n_encoder} local-llm · {n_cloud} cloud)")
    print(
        f"  corpus      {active_video_label} · "
        f"{len(cached_audio_doc_ids)} audio-cached available · "
        f"{active_chunks} benchmark chunks in scope"
    )
    print(f"  scope       {scope_label} · timeout={args.timeout}s · regen={args.regen} · audit={args.audit_log}")
    print(f"  chunker     chunk_size={chunk_size_str}")
    print(f"  output      {store.runs_dir.relative_to(ROOT)}/<run-id>/")
    print(f"{'═' * 78}\n")

    # Create a run for this benchmark session (pipeline_label resolved above for the header)
    run = store.create_run(label=pipeline_label)

    if args.regen:
        # Clear legacy extraction cached artifacts
        store.clean_extractions()
        print(f"  cleared extraction artifacts → {run.dir.relative_to(ROOT)}")
        print()

    # ── Live results table — one row per model as it finishes ───────────────
    HEADER = (
        f"  {'#':>2} {'model':<22} {'tier':<8} {'status':<8} "
        f"{'mentions':>9} {'spo':>5} {'time':>9} {'tok/s':>7} {'calls':>6} {'retry':>5} {'err':>4}"
    )
    print(HEADER)
    print(f"  {'─' * len(HEADER.lstrip())}")

    def _tier_label(tags: list[str]) -> str:
        if "encoder" in tags:
            return "ENC"
        if "extraction-specialist" in tags:
            return "SPEC"
        if "cloud" in tags:
            return "CLOUD"
        if "tier1" in tags:
            return "T1"
        if "tier2" in tags:
            return "T2"
        return "LLM"

    def _row(idx: int, name: str, tier: str, status: str, fixture: dict | None) -> None:
        if fixture:
            s = fixture.get("stats", {}) or {}
            mentions = s.get("mention_count", 0)
            spo = s.get("assertion_count", 0)
            duration = s.get("duration_s", 0.0)
            tok_s = s.get("tokens_per_sec", 0.0)
            calls = s.get("llm_call_count", 0) or 0
            retries = (s.get("mention_retries", 0) or 0) + (s.get("proposition_retries", 0) or 0)
            errors = s.get("errors", 0) or 0
            print(
                f"  {idx:>2} {name:<22} {tier:<8} {status:<8} "
                f"{mentions:>9} {spo:>5} {duration:>8.1f}s {tok_s:>7.0f} {calls:>6} {retries:>5} {errors:>4}",
                flush=True,
            )
        else:
            print(
                f"  {idx:>2} {name:<22} {tier:<8} {status:<8} "
                f"{'—':>9} {'—':>5} {'—':>9} {'—':>7} {'—':>6} {'—':>5} {'—':>4}",
                flush=True,
            )

    results = []
    t0 = time.monotonic()

    for idx, cfg in enumerate(models, 1):
        tier = _tier_label(cfg.tags)
        # Skip cloud if no key
        if "cloud" in cfg.tags:
            api_key = cfg.api_key or os.environ.get("LLM_API_KEY", "")
            if not api_key:
                _row(idx, cfg.name, tier, "skip", None)
                continue

        # Check cache
        cached = store.load_fixture(f"extraction_{cfg.model}")
        if cached and not args.regen:
            results.append({"model": cfg.name, "fixture": cached, "tags": cfg.tags})
            run.save_extraction(cfg.model, cached)
            _row(idx, cfg.name, tier, "cached", cached)
            continue

        # Check endpoint
        if cfg.base_url and "cloud" not in cfg.tags:
            import urllib.request

            try:
                urllib.request.urlopen(urllib.request.Request(f"{cfg.base_url}/models", method="GET"), timeout=3)
            except Exception:
                _row(idx, cfg.name, tier, "no-endpt", None)
                continue

        # In-flight marker (overwritten by the result row when subprocess returns)
        sys.stdout.write(
            f"  {idx:>2} {cfg.name:<22} {tier:<8} {'running…':<8} "
            f"{'…':>9} {'…':>5} {'…':>9} {'…':>7} {'…':>6} {'…':>5} {'…':>4}\r"
        )
        sys.stdout.flush()
        fixture = _run_model(cfg, args.timeout, args.audit_log, store, all_videos=args.all_videos)
        if fixture:
            results.append({"model": cfg.name, "fixture": fixture, "tags": cfg.tags})
            _row(idx, cfg.name, tier, "ok", fixture)

            # Save to run directory
            run.save_extraction(cfg.model, fixture)

            # Save audit log
            if args.audit_log:
                stats = fixture.get("stats", {}) or {}
                audit_events = stats.get("audit_events", [])
                if audit_events:
                    run.save_audit_log(
                        cfg.name,
                        {
                            "model": cfg.model,
                            "name": cfg.name,
                            "tags": cfg.tags,
                            "stats": {k: v for k, v in stats.items() if k != "audit_events"},
                            "audit_events": audit_events,
                            "event_count": len(audit_events),
                        },
                    )

            # Save incremental report
            _save_incremental_report(results, store)
        else:
            _row(idx, cfg.name, tier, "FAIL", None)

        # Unload local model from Ollama VRAM to free memory for next model
        if "cloud" not in cfg.tags and "encoder" not in cfg.tags:
            subprocess.run(["ollama", "stop", cfg.model], capture_output=True, timeout=10)

    total_time = time.monotonic() - t0

    # Generate ensemble ground truth if --full
    if args.ensemble_gt:
        ner_override = args.ner_models.split(",") if getattr(args, "ner_models", None) else None
        spo_override = args.spo_models.split(",") if getattr(args, "spo_models", None) else None
        _run_ensemble_gt(store, ner_models=ner_override, spo_models=spo_override)

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
            "exgraph_enabled": os.environ.get("EXGRAPH_ENABLED", "false"),
        }
    )

    # Copy audit logs to top level for viewer
    if args.audit_log:
        store.copy_audit_logs_to_top_level(run)

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
    print(f"  artifacts   {run.dir.relative_to(ROOT)}/")
    print(f"  report      {(store.root / 'benchmark-report.json').relative_to(ROOT)}")
    if args.audit_log:
        print(f"  audit-logs  {run.audit_dir.relative_to(ROOT)}/")
    print(f"{'═' * 78}")

    # Print the full report
    if results:
        print_benchmark_report(results)


if __name__ == "__main__":
    main()
