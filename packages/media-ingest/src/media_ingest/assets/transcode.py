"""Stage 2.5: Transcode media to AV1 using Intel QSV hardware encoding.

Sits between media_metadata and media_documents. Replaces original files
in-place with ultra-compressed AV1 versions. Recompresses even AV1 files
that are still over the target bitrate. Audio streams copied losslessly.
"""

import os
import subprocess
import time
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import (
    ASSET_RECORDS_PROCESSED,
    TRANSCODE_COMPRESSION_RATIO,
    TRANSCODE_DURATION,
    TRANSCODE_SAVED_BYTES,
)
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.assets.discovery import NFS_VOLUMES_CONFIG
from media_ingest.config import MediaIngestConfig

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Target: any video over 1.5 Mbps gets re-encoded (even if already AV1).
# YouTube AV1 typically 1-2 Mbps for 1080p talking-head content.
TARGET_MAX_BITRATE = 1_500_000

# QSV transcode needs GPU + NFS write access
TRANSCODE_K8S_CONFIG = {
    **NFS_VOLUMES_CONFIG,
    "dagster-k8s/config": {
        **NFS_VOLUMES_CONFIG["dagster-k8s/config"],
        "container_config": {
            **NFS_VOLUMES_CONFIG["dagster-k8s/config"]["container_config"],
            "resources": {
                "requests": {"cpu": "1", "memory": "4Gi", "gpu.intel.com/i915": "1"},
                "limits": {"cpu": "4", "memory": "8Gi", "gpu.intel.com/i915": "1"},
            },
        },
    },
}


def _is_already_av1(file_info: dict) -> bool:
    """Check if file is already AV1 encoded."""
    meta = file_info.get("metadata", {})
    return meta.get("video_codec", "").lower() in ("av1", "av1_qsv")


def _needs_transcode(file_info: dict, target_bitrate: int = TARGET_MAX_BITRATE) -> tuple[bool, str]:
    """Decide if a video file needs re-encoding.

    Returns (needs_transcode, reason). Always transcodes non-AV1. Re-encodes
    AV1 files whose bitrate exceeds the target.
    """
    meta = file_info.get("metadata", {})
    if not _is_already_av1(file_info):
        return True, f"non-AV1 codec ({meta.get('video_codec', 'unknown')})"

    bitrate = int(meta.get("bit_rate", 0))
    if bitrate > target_bitrate:
        mbps = bitrate / 1_000_000
        return True, f"AV1 but {mbps:.1f} Mbps > {target_bitrate / 1_000_000:.1f} Mbps target"

    return False, "AV1 already at target bitrate"


def _transcode_to_av1(
    input_path: str,
    context: AssetExecutionContext,
    preset: str = "veryfast",
    global_quality: int = 35,
) -> dict[str, Any]:
    """Transcode a video file to AV1 using QSV, replacing the original.

    Args:
        input_path: Path to the source video file
        preset: Encoding speed preset (veryfast, faster, fast, medium, slow)
        global_quality: Quality level (lower = better, 25-40 typical, 35 = ultra compressed)

    Returns:
        dict with transcode results (output_path, size_before, size_after, ratio, duration)
    """
    size_before = os.path.getsize(input_path)
    base, ext = os.path.splitext(input_path)
    temp_output = f"{base}_av1_temp.mkv"

    cmd = [
        "ffmpeg",
        "-y",
        "-hwaccel",
        "qsv",
        "-hwaccel_output_format",
        "qsv",
        "-i",
        input_path,
        "-c:v",
        "av1_qsv",
        "-preset",
        preset,
        "-global_quality",
        str(global_quality),
        "-c:a",
        "copy",  # preserve audio losslessly
        "-c:s",
        "copy",  # preserve subtitles
        "-map",
        "0",  # keep all streams
        temp_output,
    ]

    start = time.monotonic()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=7200,  # 2 hour timeout per file
    )
    duration = time.monotonic() - start

    if result.returncode != 0:
        # Clean up temp file on failure
        if os.path.exists(temp_output):
            os.unlink(temp_output)
        return {
            "error": result.stderr[-500:] if result.stderr else "ffmpeg failed",
            "duration_s": round(duration, 1),
        }

    size_after = os.path.getsize(temp_output)

    # Replace original with transcoded version
    os.replace(temp_output, input_path)

    ratio = size_before / size_after if size_after > 0 else 0
    saved_mb = (size_before - size_after) / (1024 * 1024)

    return {
        "size_before": size_before,
        "size_after": size_after,
        "compression_ratio": round(ratio, 2),
        "saved_mb": round(saved_mb, 1),
        "duration_s": round(duration, 1),
    }


@asset(
    group_name="media_ingest",
    description="Transcode video files to AV1 using Intel QSV (ultra compressed, replaces originals)",
    compute_kind="ffmpeg",
    metadata={"layer": "silver"},
    op_tags=TRANSCODE_K8S_CONFIG,
)
def media_transcode(
    context: AssetExecutionContext,
    config: MediaIngestConfig,
    media_metadata: list[dict[str, Any]],
) -> Output[list[dict[str, Any]]]:
    with trace_operation(
        "media_transcode",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "silver",
            "record_count": len(media_metadata),
        },
    ):
        video_files = [f for f in media_metadata if f.get("metadata", {}).get("has_video")]
        audio_only = [f for f in media_metadata if not f.get("metadata", {}).get("has_video")]

        to_transcode = []
        already_compressed = []
        for f in video_files:
            needs, reason = _needs_transcode(f)
            if needs:
                to_transcode.append(f)
                logger.info("Will transcode %s — %s", f["filename"], reason)
            else:
                already_compressed.append(f)

        logger.info(
            "media_transcode: %d total files (%d video, %d already compressed, %d to transcode, %d audio-only)",
            len(media_metadata),
            len(video_files),
            len(already_compressed),
            len(to_transcode),
            len(audio_only),
        )
        context.log.info(
            f"Transcode: {len(to_transcode)} to encode, "
            f"{len(already_compressed)} already at target bitrate, {len(audio_only)} audio-only"
        )

        total_saved_mb = 0.0
        errors = 0
        transcode_start = time.monotonic()

        for i, file_info in enumerate(to_transcode):
            path = file_info["path"]
            fname = file_info["filename"]
            size_mb = file_info.get("size_bytes", 0) / (1024 * 1024)
            context.log.info(f"Transcoding [{i + 1}/{len(to_transcode)}]: {fname} ({size_mb:.1f} MiB)")
            logger.info("Transcoding file=%s path=%s", fname, path)

            result = _transcode_to_av1(path, context)

            if "error" in result:
                context.log.warning(f"Transcode failed for {fname}: {result['error'][:200]}")
                logger.error("Transcode failed file=%s error=%s", fname, result["error"][:200])
                errors += 1
            else:
                total_saved_mb += result["saved_mb"]
                # Update metadata to reflect new codec
                file_info["metadata"]["video_codec"] = "av1"
                file_info["metadata"]["transcode"] = result
                file_info["size_bytes"] = result["size_after"]

                # Record transcode metrics
                TRANSCODE_DURATION.observe(result["duration_s"])
                TRANSCODE_COMPRESSION_RATIO.observe(result["compression_ratio"])
                saved_bytes = result["size_before"] - result["size_after"]
                if saved_bytes > 0:
                    TRANSCODE_SAVED_BYTES.inc(saved_bytes)

                context.log.info(
                    f"Transcoded {fname}: "
                    f"{result['compression_ratio']}x compression, "
                    f"saved {result['saved_mb']}MB in {result['duration_s']}s"
                )

        # Pass through all files (transcoded + already AV1 + audio-only)
        all_files = media_metadata

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_transcode", layer="silver").inc(
            len(to_transcode)
        )
        total_transcode_time = time.monotonic() - transcode_start
        logger.info(
            "media_transcode complete: %d transcoded (%d errors), saved %.1f MB total",
            len(to_transcode) - errors,
            errors,
            total_saved_mb,
        )
        context.log.info(
            f"Transcode complete: {len(to_transcode) - errors}/{len(to_transcode)} encoded in {total_transcode_time:.0f}s, "
            f"{errors} errors, saved {total_saved_mb:.0f} MiB"
        )

        return Output(
            all_files,
            metadata={
                "total_files": len(all_files),
                "transcoded": len(to_transcode) - errors,
                "already_compressed": len(already_compressed),
                "audio_only": len(audio_only),
                "errors": errors,
                "target_max_mbps": MetadataValue.float(TARGET_MAX_BITRATE / 1_000_000),
                "total_saved_mb": MetadataValue.float(total_saved_mb),
            },
        )
