"""Stage 1: Scan NFS directories for media files."""

import os
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from media_ingest.config import MediaIngestConfig

NFS_VOLUMES_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "volume_mounts": [
                {"name": "metube-downloads", "mountPath": "/data/metube", "readOnly": True},
                {"name": "tubesync-downloads", "mountPath": "/data/tubesync", "readOnly": True},
            ]
        },
        "pod_spec_config": {
            "volumes": [
                {"name": "metube-downloads", "persistentVolumeClaim": {"claimName": "metube-downloads"}},
                {"name": "tubesync-downloads", "persistentVolumeClaim": {"claimName": "tubesync-downloads"}},
            ]
        },
    }
}


def _scan_directory(root: str, extensions: set[str]) -> list[dict[str, Any]]:
    """Walk a directory tree and collect media file info."""
    files = []
    if not os.path.isdir(root):
        return files
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in extensions:
                full_path = os.path.join(dirpath, fname)
                stat = os.stat(full_path)
                files.append({
                    "path": full_path,
                    "filename": fname,
                    "extension": ext,
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "source_dir": root,
                })
    return files


@asset(
    group_name="media_ingest",
    description="Scan NFS download directories for media files",
    compute_kind="filesystem",
    metadata={"layer": "bronze"},
    op_tags=NFS_VOLUMES_CONFIG,
)
def media_files(
    context: AssetExecutionContext, config: MediaIngestConfig
) -> Output[list[dict[str, Any]]]:
    extensions = {e.strip() for e in config.extensions.split(",")}

    all_files: list[dict[str, Any]] = []
    for scan_path in [config.metube_path, config.tubesync_path]:
        found = _scan_directory(scan_path, extensions)
        context.log.info(f"Found {len(found)} media files in {scan_path}")
        all_files.extend(found)

    total_size = sum(f["size_bytes"] for f in all_files)
    context.log.info(f"Total: {len(all_files)} files, {total_size / (1024**3):.2f} GiB")

    return Output(
        all_files,
        metadata={
            "file_count": len(all_files),
            "total_size_gib": round(total_size / (1024**3), 2),
            "by_extension": MetadataValue.json(
                {ext: sum(1 for f in all_files if f["extension"] == ext) for ext in extensions if any(f["extension"] == ext for f in all_files)}
            ),
            "by_source": MetadataValue.json(
                {path: sum(1 for f in all_files if f["source_dir"] == path) for path in [config.metube_path, config.tubesync_path]}
            ),
        },
    )
