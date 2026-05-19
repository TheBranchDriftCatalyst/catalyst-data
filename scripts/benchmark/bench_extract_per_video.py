#!/usr/bin/env python3
"""Run LLM extraction across all videos in audio_manifest.yaml for ONE model.

Invoked as a subprocess by ``benchmark_harness.py`` when ``--all-videos`` is
set. Reads LLM_MODEL / LLM_BASE_URL / LLM_API_KEY / LLM_STRUCTURED_METHOD
from env (set by the harness's ``_run_model`` per-model env block).

For each video in the manifest:
  1. Load chunks from the medallion tree at
     ``.test-output/media-ingest/gold/media_ingest/media/media_chunks/<doc_id>/data.jsonl``
     (populated by ``task bench:chunks:regen`` — the integration test that
     materializes the production ``media_chunks`` Dagster asset).
  2. Run dagster_io.extraction.extract_validated against those chunks.
  3. Save extraction artifact at runs/<run-id>/extractions/<doc_id>/extraction_<model>.json.

If chunks are missing for a doc_id, the video is skipped with a warning
(re-run ``task bench:chunks:regen`` to regenerate).

Stdout is consumed by the harness — keep formatting predictable. Per-video
lines look like::

    [1/7] demo-video: 53 chunks → 47 mentions, 12 assertions in 4.3s

Stops with non-zero exit code only on hard failure (manifest missing, no
videos processable). Per-video extraction errors are logged and the run
continues.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "packages" / "media-ingest" / "src"))
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from tests.shared.store import BenchmarkStore  # noqa: E402

from dagster_io import TextChunk  # noqa: E402
from dagster_io.extraction import extract_validated  # noqa: E402

FIXTURE_DIR = ROOT / "packages" / "media-ingest" / "tests" / "fixtures"
MANIFEST = FIXTURE_DIR / "audio_manifest.yaml"
MEDALLION_CHUNKS_ROOT = ROOT / ".test-output" / "media-ingest" / "gold" / "media_ingest" / "media" / "media_chunks"


def _load_chunks(doc_id: str) -> list[TextChunk]:
    """Read chunks for a doc from the medallion tree written by media_chunks asset."""
    p = MEDALLION_CHUNKS_ROOT / doc_id / "data.jsonl"
    if not p.exists():
        return []
    return [TextChunk(**json.loads(line)) for line in p.read_text().splitlines() if line.strip()]


def _run_video(doc_id: str, store: BenchmarkStore, model: str, run_label: str | None) -> dict | None:
    chunks = _load_chunks(doc_id)
    if not chunks:
        print(f"  ⚠️  {doc_id}: no chunks at {MEDALLION_CHUNKS_ROOT}/{doc_id} (run task bench:chunks:regen)", flush=True)
        return None

    start = time.monotonic()
    try:
        result = extract_validated(chunks, code_location="media_ingest", max_concurrency=1)
        mentions, assertions = result.mentions, result.assertions
    except Exception as e:
        print(f"  ✗ {doc_id}: extraction error — {type(e).__name__}: {e}", flush=True)
        return None
    duration = time.monotonic() - start

    # Wave 1 Step 3 (bead llm-g0b): ``ExtractionResult.stats`` replaces
    # the deleted ``last_stats`` side channel. SPO-LLM-era counters
    # (mention_retries, proposition_retries, llm_call_count) don't
    # exist on AMR — they're dropped from the fixture below.
    pipeline_stats = result.stats
    total_input_chars = sum(len(c.text) for c in chunks)
    est_input_tokens = total_input_chars // 4
    est_output_tokens = (len(mentions) + len(assertions)) * 50
    est_total_tokens = est_input_tokens + est_output_tokens
    tokens_per_sec = est_total_tokens / duration if duration > 0 else 0

    return {
        "doc_id": doc_id,
        "model": model,
        "run_label": run_label,
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "structured_method": os.environ.get("LLM_STRUCTURED_METHOD", "function_calling"),
        "mentions": [m.model_dump(mode="json") for m in mentions],
        "assertions": [a.model_dump(mode="json") for a in assertions],
        "stats": {
            "doc_id": doc_id,
            "chunk_count": len(chunks),
            "duration_s": round(duration, 1),
            "total_input_chars": total_input_chars,
            "est_total_tokens": est_total_tokens,
            "tokens_per_sec": round(tokens_per_sec, 1),
            "mention_count": len(mentions),
            "assertion_count": len(assertions),
            "errors": pipeline_stats.get("errors", 0),
            "pipeline": pipeline_stats.get("pipeline", ""),
            "audit_events": result.audit_events if os.environ.get("SAVE_AUDIT_LOG") else [],
        },
    }


def main() -> int:
    model = os.environ.get("LLM_MODEL")
    if not model:
        print("error: LLM_MODEL env not set", file=sys.stderr)
        return 2

    if not MANIFEST.exists():
        print(f"error: manifest missing — {MANIFEST}", file=sys.stderr)
        return 2

    manifest = yaml.safe_load(MANIFEST.read_text()) or {}
    videos = manifest.get("videos") or []
    if not videos:
        print("error: no videos in manifest", file=sys.stderr)
        return 2

    # Optional --only filter (comma-separated doc_ids); used by harness when narrowing
    only = os.environ.get("BENCH_ONLY_DOC_IDS")
    if only:
        keep = set(only.split(","))
        videos = [v for v in videos if v["doc_id"] in keep]

    store = BenchmarkStore()
    run = store.load_run("latest")
    if run is None:
        # Harness owns the run; if missing, write to top-level extractions/ for back-compat
        print("  (no latest run — writing to BenchmarkStore.extractions_dir)", flush=True)

    print(f"\n  Running {model} across {len(videos)} video(s)...", flush=True)
    success = 0
    aggregate_mentions = 0
    aggregate_assertions = 0
    aggregate_chunks = 0
    aggregate_duration = 0.0
    per_video_summary: list[dict] = []

    for i, entry in enumerate(videos, 1):
        doc_id = entry["doc_id"]
        print(f"  [{i}/{len(videos)}] {doc_id}: extracting...", flush=True)
        result = _run_video(doc_id, store, model, getattr(run, "dir", None) and run.dir.name)
        if not result:
            continue

        # Persist per-doc-id
        if run is not None:
            run.save_extraction(model, result, doc_id=doc_id)
        else:
            store.save_extraction(model, result, doc_id=doc_id)

        s = result["stats"]
        success += 1
        aggregate_mentions += s["mention_count"]
        aggregate_assertions += s["assertion_count"]
        aggregate_chunks += s["chunk_count"]
        aggregate_duration += s["duration_s"]
        per_video_summary.append(
            {
                "doc_id": doc_id,
                "chunks": s["chunk_count"],
                "mentions": s["mention_count"],
                "assertions": s["assertion_count"],
                "duration_s": s["duration_s"],
            }
        )
        print(
            f"  [{i}/{len(videos)}] {doc_id}: {s['chunk_count']} chunks → "
            f"{s['mention_count']} mentions, {s['assertion_count']} assertions in {s['duration_s']:.1f}s",
            flush=True,
        )

    # Stash an aggregate summary the harness can pick up via load_extraction (no doc_id)
    aggregate = {
        "model": model,
        "all_videos": True,
        "doc_ids": [s["doc_id"] for s in per_video_summary],
        "mentions": [],  # full per-video mentions are in per-doc-id files; flatten only if needed
        "assertions": [],
        "stats": {
            "video_count": success,
            "chunk_count": aggregate_chunks,
            "mention_count": aggregate_mentions,
            "assertion_count": aggregate_assertions,
            "duration_s": round(aggregate_duration, 1),
            "per_video": per_video_summary,
        },
    }
    if run is not None:
        run.save_extraction(model, aggregate)  # flat key — harness can load_extraction(model) without doc_id
    else:
        store.save_extraction(model, aggregate)

    print(
        f"\n  Aggregate: {success}/{len(videos)} videos, "
        f"{aggregate_chunks} chunks → {aggregate_mentions} mentions, {aggregate_assertions} assertions "
        f"in {aggregate_duration:.1f}s",
        flush=True,
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
