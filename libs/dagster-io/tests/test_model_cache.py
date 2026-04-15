"""Tests for node-local model cache utility."""

from __future__ import annotations

import os

from dagster_io.model_cache import cached_model_path


def test_cache_hit(tmp_path):
    """If model already in local cache, returns local path immediately."""
    nfs_dir = tmp_path / "nfs" / "my-model"
    nfs_dir.mkdir(parents=True)
    (nfs_dir / "config.json").write_text('{"model": "test"}')

    cache_dir = tmp_path / "cache"
    local_model = cache_dir / "my-model"
    local_model.mkdir(parents=True)
    (local_model / "config.json").write_text('{"model": "test"}')

    result = cached_model_path(str(nfs_dir), str(cache_dir))
    assert result == str(local_model)


def test_cache_miss_copies_from_nfs(tmp_path):
    """If not cached, copies from NFS to local and returns local path."""
    nfs_dir = tmp_path / "nfs" / "my-model"
    nfs_dir.mkdir(parents=True)
    (nfs_dir / "weights.bin").write_bytes(b"fake model weights" * 100)
    (nfs_dir / "config.json").write_text('{"model": "test"}')

    cache_dir = tmp_path / "cache"

    result = cached_model_path(str(nfs_dir), str(cache_dir))
    assert result == str(cache_dir / "my-model")
    assert (cache_dir / "my-model" / "weights.bin").exists()
    assert (cache_dir / "my-model" / "config.json").exists()


def test_nfs_path_missing_returns_nfs_path(tmp_path):
    """If NFS path doesn't exist, returns it unchanged (caller handles error)."""
    result = cached_model_path(str(tmp_path / "nonexistent"))
    assert result == str(tmp_path / "nonexistent")


def test_cache_dir_not_writable_falls_back_to_nfs(tmp_path):
    """If local cache is not writable, falls back to NFS path."""
    nfs_dir = tmp_path / "nfs" / "my-model"
    nfs_dir.mkdir(parents=True)
    (nfs_dir / "config.json").write_text("{}")

    # Use a path that can't be created (nested under a file, not a dir)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    cache_dir = str(blocker) + "/cache"

    result = cached_model_path(str(nfs_dir), cache_dir)
    assert result == str(nfs_dir)


def test_nested_model_structure(tmp_path):
    """Model directories with subdirectories are copied correctly."""
    nfs_dir = tmp_path / "nfs" / "whisper-large-v3"
    (nfs_dir / "subdir").mkdir(parents=True)
    (nfs_dir / "model.bin").write_bytes(b"x" * 50)
    (nfs_dir / "subdir" / "vocab.json").write_text("{}")

    cache_dir = tmp_path / "cache"
    result = cached_model_path(str(nfs_dir), str(cache_dir))

    assert os.path.isfile(os.path.join(result, "model.bin"))
    assert os.path.isfile(os.path.join(result, "subdir", "vocab.json"))
