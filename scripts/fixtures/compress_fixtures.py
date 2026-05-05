#!/usr/bin/env python3
"""Compress video fixtures in a target folder.

Calls the production media_ingest.assets.transcode dispatcher with a backend
suited to the local machine:

- ``videotoolbox-hevc`` (default on macOS): Apple Silicon hardware HEVC encoder.
  10-20x faster than libsvtav1 with ~1.3-1.5x larger output. Best for fixture
  prep on M-series Macs.
- ``svt-av1``: SVT-AV1 software AV1. Smallest output, slowest. Use on CI or
  when AV1 output is required.

Files are replaced in place. Run this after dropping new demo videos into
tests/fixtures/media-ingest/ to keep the repo small.

Usage::

    python scripts/fixtures/compress_fixtures.py tests/fixtures/media-ingest/
    python scripts/fixtures/compress_fixtures.py PATH --backend svt-av1 --crf 45
    python scripts/fixtures/compress_fixtures.py PATH --scale 360 --audio-mono-16k
    python scripts/fixtures/compress_fixtures.py PATH --max-duration 600

Defaults: videotoolbox-hevc on macOS / svt-av1 elsewhere, scale to 480p,
Opus 64kbps stereo audio.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Allow running from repo root without installing
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "packages" / "media-ingest" / "src"))

from media_ingest.assets.transcode import _transcode_video  # noqa: E402

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}


def _default_backend() -> str:
    """Pick the best backend for the local platform."""
    return "videotoolbox-hevc" if sys.platform == "darwin" else "svt-av1"


def _human(n_bytes: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TiB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="Folder containing video files to compress")
    parser.add_argument(
        "--backend",
        choices=["videotoolbox-hevc", "svt-av1"],
        default=_default_backend(),
        help=f"Encoder backend. Default {_default_backend()} (auto-detected for this platform).",
    )
    parser.add_argument("--crf", type=int, default=50, help="SVT-AV1 CRF (30-63, higher=smaller). Default 50.")
    parser.add_argument(
        "--vt-quality",
        type=int,
        default=60,
        help="VideoToolbox HEVC quality 0-100 (lower=smaller). Default 60. Ignored if --vt-bitrate is set.",
    )
    parser.add_argument(
        "--vt-bitrate",
        default=None,
        help="VideoToolbox target bitrate (e.g. 200k, 1M). Predictable size. Overrides --vt-quality.",
    )
    parser.add_argument("--preset", type=int, default=8, help="SVT-AV1 preset (0-13, lower=slower+smaller). Default 8.")
    parser.add_argument(
        "--scale",
        type=int,
        default=480,
        help="Downscale video to this height (px), preserving aspect. Set 0 to disable. Default 480.",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=None,
        help="Trim each output to this many seconds from start. Default: keep full duration.",
    )
    parser.add_argument("--audio-bitrate", default="64k", help="Opus audio bitrate. Default 64k.")
    parser.add_argument(
        "--audio-mono-16k",
        action="store_true",
        help="Downmix audio to mono 16kHz (smallest; matches Whisper input).",
    )
    parser.add_argument(
        "--keep-originals",
        action="store_true",
        help="Copy each file to <name>.orig before compressing (default: replace in place, no backup).",
    )
    parser.add_argument("--dry-run", action="store_true", help="List files that would be compressed; do nothing.")
    args = parser.parse_args()

    folder: Path = args.folder.resolve()
    if not folder.is_dir():
        print(f"error: not a directory: {folder}", file=sys.stderr)
        return 2

    videos = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    if not videos:
        print(f"No video files in {folder} (looked for: {', '.join(sorted(VIDEO_EXTS))})")
        return 0

    print(f"Found {len(videos)} video(s) in {folder}")
    if args.backend == "videotoolbox-hevc":
        rate_label = f"bitrate={args.vt_bitrate}" if args.vt_bitrate else f"q={args.vt_quality}"
        settings_label = f"backend=videotoolbox-hevc {rate_label}"
    else:
        settings_label = f"backend=svt-av1 preset={args.preset} crf={args.crf}"
    if args.scale > 0:
        settings_label += f" scale={args.scale}p"
    if args.max_duration:
        settings_label += f" max_duration={args.max_duration}s"
    print(f"Settings: {settings_label} audio={args.audio_bitrate}", end="")
    print(" mono-16k" if args.audio_mono_16k else " stereo")
    if args.keep_originals:
        print("Keeping .orig backups alongside each file")
    print()

    if args.dry_run:
        for v in videos:
            print(f"  [dry-run] {v.name}  ({_human(v.stat().st_size)})")
        return 0

    total_before = 0
    total_after = 0
    errors = 0

    for i, video in enumerate(videos, 1):
        size_before = video.stat().st_size
        total_before += size_before
        print(f"[{i}/{len(videos)}] {video.name}  {_human(size_before)} → ...", flush=True)

        if args.keep_originals:
            shutil.copy2(video, video.with_suffix(video.suffix + ".orig"))

        backend_kwargs: dict = {
            "audio_bitrate": args.audio_bitrate,
            "audio_mono_16k": args.audio_mono_16k,
            "scale_height": args.scale if args.scale > 0 else None,
            "max_duration_s": args.max_duration,
        }
        if args.backend == "videotoolbox-hevc":
            if args.vt_bitrate:
                backend_kwargs["bitrate"] = args.vt_bitrate
                backend_kwargs["quality"] = None
            else:
                backend_kwargs["quality"] = args.vt_quality
        else:  # svt-av1
            backend_kwargs["svt_preset"] = args.preset
            backend_kwargs["crf"] = args.crf

        result = _transcode_video(str(video), backend=args.backend, **backend_kwargs)

        if "error" in result:
            print(f"   FAILED in {result['duration_s']}s: {result['error'][:120]}")
            errors += 1
            total_after += size_before  # didn't shrink
            continue

        total_after += result["size_after"]
        print(
            f"   {_human(result['size_before'])} → {_human(result['size_after'])}  "
            f"({result['compression_ratio']}x, saved {result['saved_mb']:.1f} MiB) "
            f"in {result['duration_s']:.1f}s"
        )

    print()
    print("─" * 60)
    saved_total = (total_before - total_after) / (1024 * 1024)
    overall_ratio = total_before / total_after if total_after > 0 else 0
    print(
        f"Done: {len(videos) - errors}/{len(videos)} compressed  "
        f"{_human(total_before)} → {_human(total_after)}  "
        f"({overall_ratio:.2f}x overall, saved {saved_total:.1f} MiB)"
    )
    if errors:
        print(f"  {errors} error(s) — see ffmpeg output above")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
