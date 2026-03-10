"""Shared S3/MinIO IO manager for Dagster pipelines."""

from dagster_io.io_manager import MinioIOManager
from dagster_io.manifest import AssetManifest, MaterializationRecord

__all__ = ["MinioIOManager", "AssetManifest", "MaterializationRecord"]
