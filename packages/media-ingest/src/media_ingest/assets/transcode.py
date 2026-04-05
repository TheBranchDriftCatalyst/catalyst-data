"""Stage 2.5: Transcode media to AV1 using Intel QSV hardware encoding.

Sits between media_metadata and media_documents. Replaces original files
in-place with ultra-compressed AV1 versions. Skips files already in AV1.
Audio streams are copied without re-encoding to preserve transcription quality.
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
        "ffmpeg", "-y",
        "-hwaccel", "qsv",
        "-hwaccel_output_format", "qsv",
        "-i", input_path,
        "-c:v", "av1_qsv",
        "-preset", preset,
        "-global_quality", str(global_quality),
        "-c:a", "copy",        # preserve audio losslessly
        "-c:s", "copy",        # preserve subtitles
        "-map", "0",           # keep all streams
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
    with trace_operation("media_transcode", tracer, {"code_location": "media_ingest", "layer": "silver", "record_count": len(media_metadata)}):
        video_files = [f for f in media_metadata if f.get("metadata", {}).get("has_video")]
        already_av1 = [f for f in video_files if _is_already_av1(f)]
        to_transcode = [f for f in video_files if not _is_already_av1(f)]
        audio_only = [f for f in media_metadata if not f.get("metadata", {}).get("has_video")]

        logger.info(
            "media_transcode: %d total files (%d video, %d already AV1, %d to transcode, %d audio-only)",
            len(media_metadata), len(video_files), len(already_av1), len(to_transcode), len(audio_only),
        )
        context.log.info(
            f"Transcode: {len(to_transcode)} files to encode, "
            f"{len(already_av1)} already AV1, {len(audio_only)} audio-only (skipped)"
        )

        total_saved_mb = 0.0
        errors = 0

        for file_info in to_transcode:
            path = file_info["path"]
            fname = file_info["filename"]
            context.log.info(f"Transcoding: {fname}")
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

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_transcode", layer="silver").inc(len(to_transcode))
        logger.info(
            "media_transcode complete: %d transcoded (%d errors), saved %.1f MB total",
            len(to_transcode) - errors, errors, total_saved_mb,
        )
        context.log.info(
            f"Transcode complete: {len(to_transcode) - errors} encoded, "
            f"{errors} errors, saved {total_saved_mb:.0f} MB"
        )

        return Output(
            all_files,
            metadata={
                "total_files": len(all_files),
                "transcoded": len(to_transcode) - errors,
                "already_av1": len(already_av1),
                "audio_only": len(audio_only),
                "errors": errors,
                "total_saved_mb": MetadataValue.float(total_saved_mb),
            },
        )
