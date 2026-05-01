#!/usr/bin/env python3
"""Regenerate per-video audio cache (transcription + diarization).

Reads tests/fixtures/media-ingest/audio_manifest.yaml, runs the production
transcription dispatcher (_select_backend) + diarization pipeline (_run_diarization
+ _assign_speakers) on each video, and saves outputs to per-doc-id subdirs:

    .test-output/media-ingest/pipeline-cache/<doc_id>/0_transcription.json
                                              <doc_id>/1_diarization.json

Same code path as production media_transcriptions / media_diarization assets,
so what the test exercises matches what would deploy. Backend is selected via
WHISPER_BACKEND env (mlx-whisper on Apple Silicon is the fast path).

Usage::

    HF_TOKEN=hf_xxx python scripts/regen_audio_fixtures.py
    HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper python scripts/regen_audio_fixtures.py
    HF_TOKEN=hf_xxx python scripts/regen_audio_fixtures.py --force      # regenerate even if cached
    HF_TOKEN=hf_xxx python scripts/regen_audio_fixtures.py --only demo-video,inside-the-aipac-pipeline

Audio sub-stages each video goes through:

  1. Transcription (slow, cached) — Whisper via _select_backend(MediaIngestConfig)
  2. Diarization  (slow, cached) — pyannote, MPS/CUDA/CPU auto-selected
  3. Segment merge (fast, NOT cached) — recomputed on benchmark invocation
  4. Chunking      (fast, NOT cached) — ChunkingResource.chunk_speaker_segments

Steps 1 + 2 are what this script populates. The chunker reads cached output
and runs in milliseconds when needed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Allow running from repo root without installing
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "media-ingest" / "src"))
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from media_ingest.assets.diarization import _assign_speakers, _run_diarization  # noqa: E402
from media_ingest.assets.transcription import _select_backend, _validate_transcription_fidelity  # noqa: E402
from media_ingest.config import MediaIngestConfig  # noqa: E402
from tests.shared.store import BenchmarkStore  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "media-ingest"
MANIFEST = FIXTURE_DIR / "audio_manifest.yaml"


def _build_config() -> MediaIngestConfig:
    """Same config-from-env shape as the integration test fixture."""
    return MediaIngestConfig(
        whisper_backend=os.environ.get("WHISPER_BACKEND", "faster-whisper"),
        whisper_model=os.environ.get("WHISPER_MODEL", "base"),
        whisper_device=os.environ.get("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        mlx_model_id=os.environ.get("MLX_MODEL_ID", "mlx-community/whisper-base-mlx"),
    )


def _transcribe(video_path: Path, doc_id: str, title: str, config: MediaIngestConfig) -> dict:
    sys.stdout.write("\n" + "=" * 70 + "\n")
    sys.stdout.write(f"  [regen] {doc_id} — STAGE 1/2: TRANSCRIPTION\n")
    sys.stdout.write(f"  Backend: {config.whisper_backend}\n")
    sys.stdout.write(f"  Audio:   {video_path.name}\n")
    sys.stdout.write("=" * 70 + "\n")
    sys.stdout.flush()

    print(f"  Loading {config.whisper_backend}...", flush=True)
    model, resolved_device, model_label, transcribe_fn = _select_backend(config)

    print(f"  Transcribing ({model_label} on {resolved_device})...", flush=True)
    start = time.monotonic()
    result = transcribe_fn(model, str(video_path))
    duration = time.monotonic() - start

    for w in _validate_transcription_fidelity(result, config.whisper_backend, model_label):
        print(f"\n  ⚠️  {w}", flush=True)

    print(f"  Transcription complete: {len(result['segments'])} segments in {duration:.1f}s", flush=True)
    return {
        "document_id": doc_id,
        "title": title,
        "text": " ".join(s["text"] for s in result["segments"]),
        "language": result["language"],
        "language_probability": result["language_probability"],
        "duration_s": result["duration_s"],
        "segments": result["segments"],
        "segment_count": len(result["segments"]),
        "source_path": str(video_path),
        "backend": config.whisper_backend,
        "model_label": model_label,
        "resolved_device": resolved_device,
        "transcribe_time_s": round(duration, 1),
    }


def _diarize(video_path: Path, transcription: dict, doc_id: str, store: BenchmarkStore) -> dict:
    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token:
        raise RuntimeError("HF_TOKEN not set — required for pyannote diarization")

    sys.stdout.write("\n" + "=" * 70 + "\n")
    sys.stdout.write(f"  [regen] {doc_id} — STAGE 2/2: DIARIZATION\n")
    sys.stdout.write("  Backend: pyannote.audio (auto cuda → mps → cpu)\n")
    sys.stdout.write("=" * 70 + "\n")
    sys.stdout.flush()

    # Reuse the local model cache the integration test uses
    local_cache = str(store.pipeline_cache_dir / "model_cache")
    os.makedirs(local_cache, exist_ok=True)

    print("  Running pyannote diarization (production code path)...", flush=True)
    start = time.monotonic()
    diarization, device = _run_diarization(str(video_path), hf_token, local_cache)
    segments = _assign_speakers(transcription["segments"], diarization)
    unique_speakers = {s.get("speaker") for s in segments if s.get("speaker")}
    duration = time.monotonic() - start

    print(
        f"  Diarization complete: {len(unique_speakers)} speakers on {device} in {duration:.1f}s",
        flush=True,
    )
    return {
        **transcription,
        "segments": segments,
        "speaker_count": len(unique_speakers),
        "speakers": sorted(unique_speakers) if unique_speakers else [],
        "speaker_text": None,
        "diarization_time_s": round(duration, 1),
        "diarization_device": device,
    }


def _human(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60)}s"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help=f"Path to manifest YAML (default {MANIFEST})")
    parser.add_argument("--only", default=None, help="Comma-separated list of doc_ids to process (default: all)")
    parser.add_argument(
        "--force", action="store_true", help="Regenerate even if cached transcription/diarization exists"
    )
    parser.add_argument(
        "--skip-diarization",
        action="store_true",
        help="Only run transcription (useful when HF_TOKEN unavailable)",
    )
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"error: manifest not found at {args.manifest}", file=sys.stderr)
        return 2
    manifest = yaml.safe_load(args.manifest.read_text()) or {}
    videos = manifest.get("videos") or []
    if not videos:
        print(f"error: no videos in manifest {args.manifest}", file=sys.stderr)
        return 2

    only = set(args.only.split(",")) if args.only else None
    if only:
        videos = [v for v in videos if v["doc_id"] in only]
        if not videos:
            print("error: --only filter matched 0 entries in manifest", file=sys.stderr)
            return 2

    config = _build_config()
    store = BenchmarkStore()

    print(f"\n{'─' * 70}")
    print(f"  Regenerating audio fixtures for {len(videos)} video(s)")
    print(
        f"  Backend: {config.whisper_backend}  ({config.mlx_model_id if config.whisper_backend == 'mlx-whisper' else config.whisper_model})"
    )
    print(f"  Cache:   {store.pipeline_cache_dir}")
    if args.skip_diarization:
        print("  Skipping diarization (--skip-diarization)")
    print(f"{'─' * 70}\n")

    overall_start = time.monotonic()
    succeeded = 0
    skipped = 0
    failed: list[tuple[str, str]] = []

    for entry in videos:
        doc_id = entry["doc_id"]
        title = entry.get("title", doc_id)
        video_path = (FIXTURE_DIR / entry["file"]).resolve()
        if not video_path.exists():
            print(f"  ✗ {doc_id}: source file missing — {video_path}")
            failed.append((doc_id, "source file missing"))
            continue

        # Cache check
        cached_t = store.load_pipeline_artifact("transcription", doc_id=doc_id)
        cached_d = store.load_pipeline_artifact("diarization", doc_id=doc_id)
        if not args.force and cached_t and (cached_d or args.skip_diarization):
            print(f"  ⊘ {doc_id}: already cached (use --force to regenerate)")
            skipped += 1
            continue

        try:
            if args.force or not cached_t:
                transcription = _transcribe(video_path, doc_id, title, config)
                store.save_pipeline_artifact("transcription", transcription, doc_id=doc_id)
            else:
                print(f"  ↪ {doc_id}: using cached transcription")
                transcription = cached_t

            if not args.skip_diarization and (args.force or not cached_d):
                diar = _diarize(video_path, transcription, doc_id, store)
                store.save_pipeline_artifact("diarization", diar, doc_id=doc_id)

            succeeded += 1
        except Exception as e:
            print(f"  ✗ {doc_id}: {type(e).__name__}: {e}", flush=True)
            failed.append((doc_id, f"{type(e).__name__}: {e}"))

    total = time.monotonic() - overall_start
    print(f"\n{'─' * 70}")
    print(f"  Done in {_human(total)}: {succeeded} succeeded, {skipped} skipped, {len(failed)} failed")
    if failed:
        for doc_id, msg in failed:
            print(f"    ✗ {doc_id}: {msg}")
    print(f"  Cache root: {store.pipeline_cache_dir}")
    print(f"{'─' * 70}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
