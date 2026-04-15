"""Node-local model cache — avoid re-reading large models from NFS on every step pod.

Models are stored on NFS (authoritative source) but loaded from node-local
storage when available. On first run on a node, the model is copied from
NFS to local cache. Subsequent runs on the same node skip the NFS read.

Usage:
    from dagster_io.model_cache import cached_model_path

    # Returns local path if cached, otherwise copies from NFS and returns local path.
    # Falls back to NFS path if local cache is unavailable (read-only filesystem, etc).
    model_dir = cached_model_path("/data/whisper-models/some-model", "/cache/models")
"""

from __future__ import annotations

import os
import shutil
import time

from dagster_io.logging import get_logger

logger = get_logger(__name__)

# Default local cache directory — mounted as hostPath in k8s
LOCAL_CACHE_DIR = os.environ.get("MODEL_CACHE_DIR", "/cache/models")


def cached_model_path(nfs_path: str, cache_dir: str | None = None) -> str:
    """Return a node-local cached copy of a model directory.

    If the model exists in the local cache, returns the local path immediately.
    If not, copies the entire directory from NFS to local cache, then returns
    the local path. Falls back to the NFS path if caching fails.

    Args:
        nfs_path: Path to the model on NFS (e.g. /data/whisper-models/model-name).
        cache_dir: Local cache root. Defaults to MODEL_CACHE_DIR env var or /cache/models.

    Returns:
        Path to the model directory (local cache or NFS fallback).
    """
    if not os.path.isdir(nfs_path):
        logger.warning("Model path does not exist: %s — returning as-is", nfs_path)
        return nfs_path

    cache_root = cache_dir or LOCAL_CACHE_DIR
    model_name = os.path.basename(nfs_path)
    local_path = os.path.join(cache_root, model_name)

    # Check if cache root is even available (volume mounted?)
    if not os.path.isdir(cache_root):
        logger.info(
            "Model cache directory %s not mounted (PodSecurity may block hostPath). Loading from NFS: %s",
            cache_root,
            nfs_path,
        )
        return nfs_path

    # Already cached — use it
    if os.path.isdir(local_path):
        logger.info("Model cache HIT: loading from local %s (skipping NFS)", local_path)
        return local_path

    # Cache miss — copy from NFS to local
    try:
        os.makedirs(cache_root, exist_ok=True)
        start = time.monotonic()
        logger.info("Model cache MISS: copying from NFS %s → local %s", nfs_path, local_path)
        shutil.copytree(nfs_path, local_path)
        duration = time.monotonic() - start
        size_mb = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(local_path) for f in fns) / (
            1024 * 1024
        )
        logger.info(
            "Model cache POPULATED: %.0f MB in %.1fs (%.0f MB/s) — subsequent runs will use local",
            size_mb,
            duration,
            size_mb / max(duration, 0.01),
        )
        return local_path
    except (OSError, shutil.Error) as e:
        logger.warning(
            "Model cache FAILED (falling back to NFS): %s. "
            "This is expected if the cache volume is not mounted. "
            "To enable: mount a local-path PVC at %s",
            e,
            cache_root,
        )
        if os.path.isdir(local_path):
            shutil.rmtree(local_path, ignore_errors=True)
        return nfs_path
