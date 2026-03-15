"""Stage 2: Extract metadata from media files using ffprobe."""

import json
import subprocess
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from media_ingest.assets.discovery import NFS_VOLUMES_CONFIG

logger = get_logger(__name__)


def _ffprobe(path: str) -> dict[str, Any]:
    """Run ffprobe on a file and return parsed JSON output."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"error": str(e)}


def _extract_metadata(probe: dict[str, Any]) -> dict[str, Any]:
    """Extract key metadata fields from ffprobe output."""
    fmt = probe.get("format", {})
    streams = probe.get("streams", [])

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    meta: dict[str, Any] = {
        "duration_seconds": float(fmt.get("duration", 0)),
        "format_name": fmt.get("format_name", ""),
        "bit_rate": int(fmt.get("bit_rate", 0)),
        "has_video": len(video_streams) > 0,
        "has_audio": len(audio_streams) > 0,
    }

    if video_streams:
        vs = video_streams[0]
        meta["video_codec"] = vs.get("codec_name", "")
        meta["width"] = vs.get("width", 0)
        meta["height"] = vs.get("height", 0)

    if audio_streams:
        aus = audio_streams[0]
        meta["audio_codec"] = aus.get("codec_name", "")
        meta["sample_rate"] = int(aus.get("sample_rate", 0))
        meta["channels"] = aus.get("channels", 0)

    return meta


@asset(
    group_name="media_ingest",
    description="Extract media metadata using ffprobe",
    compute_kind="ffprobe",
    metadata={"layer": "silver"},
    op_tags=NFS_VOLUMES_CONFIG,
)
def media_metadata(
    context: AssetExecutionContext,
    media_files: list[dict[str, Any]],
) -> Output[list[dict[str, Any]]]:
    logger.info("Starting media_metadata extraction for %d files", len(media_files))
    enriched = []
    errors = 0

    for file_info in media_files:
        probe = _ffprobe(file_info["path"])
        if "error" in probe:
            context.log.warning(f"ffprobe failed for {file_info['filename']}: {probe['error']}")
            file_info["metadata"] = {"error": probe["error"]}
            errors += 1
        else:
            file_info["metadata"] = _extract_metadata(probe)
        enriched.append(file_info)

    ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_metadata", layer="silver").inc(len(enriched))
    logger.info("media_metadata complete: %d files probed (%d errors)", len(enriched), errors)
    context.log.info(f"Probed {len(enriched)} files ({errors} errors)")

    return Output(
        enriched,
        metadata={
            "total_files": len(enriched),
            "errors": errors,
            "with_video": sum(1 for f in enriched if f.get("metadata", {}).get("has_video")),
            "with_audio": sum(1 for f in enriched if f.get("metadata", {}).get("has_audio")),
            "total_duration_hours": MetadataValue.float(
                round(sum(f.get("metadata", {}).get("duration_seconds", 0) for f in enriched) / 3600, 2)
            ),
        },
    )
