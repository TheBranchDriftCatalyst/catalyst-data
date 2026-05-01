#!/usr/bin/env python3
"""Regenerate per-video benchmark_chunks fixtures from cached audio output.

For each video in tests/fixtures/media-ingest/audio_manifest.yaml:
  1. Load cached diarization output (per-doc-id pipeline-cache).
  2. Run ChunkingResource.chunk_speaker_segments — same chunker as production
     media_chunks asset.
  3. Save the FULL chunk list per video — no curation here. The full set is
     what the benchmark harness's --all-videos mode iterates over.
  4. Build a merged tests/fixtures/media-ingest/benchmark_chunks.json across
     all videos (consumed by BenchmarkStore.load_benchmark_chunks).

Two flags adjust the chunker behavior at the call site:
  --no-prepend-title      drop the title prefix from chunk text. Useful for
                          benchmark inputs where the repeated title across
                          short chunks dominates the actual content.
  --min-chunk-chars N     skip chunks whose text is shorter than N chars
                          (drops trivially-short utterances like "yeah, yeah"
                          that have no extractable content). Filtering here is
                          OK because it's deterministic and content-derived,
                          unlike first-N which biased toward intros.

Curation strategies that pick a *subset* (stratified sampling, semantic
diversity, manual review) belong in a separate script and a separate fixture
file (e.g. benchmark_eval_subset.json), not in this all-chunks regen path.

Usage::

    HF_TOKEN=... python scripts/regen_benchmark_chunks.py
    python scripts/regen_benchmark_chunks.py --no-prepend-title --min-chunk-chars 80
    python scripts/regen_benchmark_chunks.py --only demo-video,saagar-x-joe-kent

Nothing here touches LLM extraction or ground truth — chunker is pure Python,
runs in milliseconds. Re-run after changing chunker config or after
regenerating audio fixtures via ``task bench:fixtures:regen``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "media-ingest" / "src"))
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from tests.shared.store import BenchmarkStore  # noqa: E402

from dagster_io import ChunkingResource  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "media-ingest"
MANIFEST = FIXTURE_DIR / "audio_manifest.yaml"
PER_VIDEO_DIR = FIXTURE_DIR / "per_video_chunks"
MERGED_FILE = FIXTURE_DIR / "benchmark_chunks.json"


def _chunk_for_video(
    doc_id: str,
    title: str,
    store: BenchmarkStore,
    *,
    prepend_title: bool,
    min_chunk_chars: int,
    embedder=None,
) -> list[dict] | None:
    """Run the production hybrid chunker (multi-speaker windowing + semantic
    refinement) against cached diarization.

    Same entry point ``media_chunks`` Dagster asset uses, so prod and bench
    can never drift on chunking. When ``embedder`` is None the refinement
    pass is skipped (caller controls cost/latency).

    Returns the per-video chunk list, or None if the audio cache is missing.
    """
    diarization = store.load_pipeline_artifact("diarization", doc_id=doc_id)
    if not diarization:
        return None

    chunking = ChunkingResource(prepend_title=prepend_title)
    chunks = chunking.chunk_with_semantic_refinement(
        diarization["segments"],
        document_id=doc_id,
        title=title,
        embedder=embedder,
        metadata={
            "source": "media_ingest",
            "language": diarization.get("language", "unknown"),
            "speaker_count": diarization.get("speaker_count", 0),
            "doc_id": doc_id,
        },
    )
    raw = [c.model_dump(mode="json") for c in chunks]
    if min_chunk_chars > 0:
        raw = [c for c in raw if len(c.get("text", "")) >= min_chunk_chars]
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help=f"Manifest YAML (default {MANIFEST})")
    parser.add_argument("--only", default=None, help="Comma-separated doc_ids to process")
    parser.add_argument(
        "--no-prepend-title",
        action="store_true",
        help=(
            "Drop the title prefix from chunk text. Recommended for benchmark inputs — "
            "the repeated title across short chunks dominates the actual content otherwise."
        ),
    )
    parser.add_argument(
        "--min-chunk-chars",
        type=int,
        default=0,
        help=(
            "Skip chunks shorter than N characters (deterministic, content-based filter). "
            "Default 0 = keep all. Try 80 to drop trivially-short utterances."
        ),
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Skip rebuilding the merged tests/fixtures/media-ingest/benchmark_chunks.json file",
    )
    parser.add_argument(
        "--no-semantic-refine",
        action="store_true",
        help=(
            "Skip the semantic refinement pass (multi-speaker windowing only). "
            "Default: ON when OPENAI_API_KEY or LLM_API_KEY is set; OFF otherwise."
        ),
    )
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"error: manifest missing — {args.manifest}", file=sys.stderr)
        return 2
    manifest = yaml.safe_load(args.manifest.read_text()) or {}
    videos = manifest.get("videos") or []

    only = set(args.only.split(",")) if args.only else None
    if only:
        videos = [v for v in videos if v["doc_id"] in only]

    store = BenchmarkStore()
    PER_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize embedder for semantic refinement, unless suppressed.
    # Auto-skip when no API key is configured so pure-bench-prep runs still
    # work offline; print clearly which mode we're in.
    embedder = None
    refine_status = "OFF"
    if not args.no_semantic_refine:
        import os as _os

        if _os.environ.get("OPENAI_API_KEY") or _os.environ.get("LLM_API_KEY"):
            try:
                from dagster_io import EmbeddingResource

                embedder = EmbeddingResource()
                # EmbeddingResource initializes its underlying langchain embeddings
                # client inside Dagster's setup_for_execution hook. Outside Dagster
                # (this script) we have to call it ourselves or .embed() AttributeErrors
                # on the unset _embeddings PrivateAttr.
                embedder.setup_for_execution(None)
                refine_status = f"ON (provider={embedder.provider} model={embedder.model})"
            except Exception as exc:  # pragma: no cover
                print(f"  warning: failed to init EmbeddingResource ({exc}); skipping semantic refinement")
                embedder = None
        else:
            refine_status = "OFF (no OPENAI_API_KEY / LLM_API_KEY in env)"

    print(f"\n{'─' * 70}")
    print(f"  Regenerating benchmark_chunks for {len(videos)} video(s)")
    print(f"  Per-video output:    {PER_VIDEO_DIR}/")
    print(f"  prepend_title:       {not args.no_prepend_title}")
    print(f"  min_chunk_chars:     {args.min_chunk_chars}")
    print(f"  semantic refinement: {refine_status}")
    print(f"{'─' * 70}\n")

    from collections import Counter as _Counter

    succeeded = 0
    skipped = 0
    total_raw = 0
    total_kept = 0
    all_chunks: list[dict] = []
    # Per-video and corpus-wide strategy histograms — surfaces how often the
    # chunker hit each tier (multi_speaker_window vs speech_pause_split vs
    # semantic_merge etc) so we can tune thresholds.
    per_video_strategies: dict[str, dict[str, int]] = {}
    corpus_strategies: _Counter = _Counter()

    for entry in videos:
        doc_id = entry["doc_id"]
        title = entry.get("title", doc_id)
        chunks = _chunk_for_video(
            doc_id,
            title,
            store,
            prepend_title=not args.no_prepend_title,
            min_chunk_chars=args.min_chunk_chars,
            embedder=embedder,
        )
        if chunks is None:
            print(f"  ⊘ {doc_id}: no diarization cache (run task bench:fixtures:regen)")
            skipped += 1
            continue

        # The chunker has already produced everything; min_chunk_chars filtering
        # happened inside _chunk_for_video. We don't re-truncate here.
        kept = len(chunks)
        # Reconstruct the pre-filter count by re-running the chunker without
        # the min_chunk_chars filter just to report the delta. Cheap: pure
        # Python, milliseconds. Skip when filter is off to avoid the work.
        if args.min_chunk_chars > 0:
            unfiltered = _chunk_for_video(
                doc_id,
                title,
                store,
                prepend_title=not args.no_prepend_title,
                min_chunk_chars=0,
                embedder=embedder,
            )
            raw = len(unfiltered) if unfiltered is not None else kept
        else:
            raw = kept
        total_raw += raw
        total_kept += kept

        out_dir = PER_VIDEO_DIR / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "benchmark_chunks.json"
        out_file.write_text(json.dumps(chunks, indent=2, default=str))
        all_chunks.extend(chunks)
        # Tally chunker strategies for this video — small breakdown immediately
        # under the per-video line so users don't have to grep the JSON.
        strat_counts = _Counter(c.get("metadata", {}).get("strategy", "?") for c in chunks)
        per_video_strategies[doc_id] = dict(strat_counts)
        corpus_strategies.update(strat_counts)
        # Average chunk length for quick eyeball check of content density
        avg_len = (sum(len(c.get("text", "")) for c in chunks) // kept) if kept else 0
        delta = f"{raw} → {kept}" if args.min_chunk_chars > 0 else f"{kept}"
        strat_summary = " · ".join(f"{s}={n}" for s, n in sorted(strat_counts.items(), key=lambda kv: -kv[1]))
        print(f"  ✓ {doc_id}: {delta} chunks (avg {avg_len}c) — {strat_summary}")
        succeeded += 1

    if not args.no_merge and all_chunks:
        MERGED_FILE.write_text(json.dumps(all_chunks, indent=2, default=str))
        print(f"\n  Merged file: {MERGED_FILE.relative_to(ROOT)} ({len(all_chunks)} chunks total)")

    print(f"\n{'─' * 70}")
    print(f"  Done: {succeeded} succeeded, {skipped} skipped")
    if args.min_chunk_chars > 0:
        print(f"  Chunks: {total_raw} raw → {total_kept} kept after min-length filter")
    else:
        print(f"  Chunks: {total_kept} total")

    # Corpus-wide chunker strategy histogram. Surfaces how the hybrid
    # chunker actually behaved across all videos so we can tune thresholds.
    if corpus_strategies:
        print(f"\n  Chunker strategy breakdown (across {succeeded} video(s)):")
        total = sum(corpus_strategies.values())
        # Stable strategy ordering — known tiers first, then anything new
        order = [
            "multi_speaker_window",
            "semantic_merge",
            "multi_speaker_split",
            "speaker_turn",
            "speech_pause_split",
            "text_split_fallback",
            "section_split",
            "subsection_split",
            "passthrough",
            "recursive",
        ]
        rest = sorted(s for s in corpus_strategies if s not in order)
        for strat in [*order, *rest]:
            n = corpus_strategies.get(strat, 0)
            if n == 0:
                continue
            pct = 100 * n / total
            print(f"    {strat:<24} {n:>5}  ({pct:>5.1f}%)")
        # Refinement-pass impact: if semantic_merge fired, show the merge ratio
        merged = corpus_strategies.get("semantic_merge", 0)
        if merged:
            print(f"\n  Semantic refinement merged {merged} chunks (~{100 * merged / total:.1f}% of final output)")
    print(f"{'─' * 70}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
