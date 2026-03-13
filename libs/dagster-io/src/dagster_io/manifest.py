"""Asset manifest tracking for materialization history."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field

MAX_MANIFEST_ENTRIES = 100


class MaterializationRecord(BaseModel):
    """Single materialization event."""

    run_id: str
    timestamp: str  # ISO 8601
    partition: str | None = None
    format: str  # jsonl, json, pkl
    count: int
    size_bytes: int


class AssetManifest(BaseModel):
    """Tracks materialization history for an asset, capped at MAX_MANIFEST_ENTRIES."""

    asset: str
    code_location: str
    layer: str = "raw"
    materializations: list[MaterializationRecord] = Field(default_factory=list)

    def add_materialization(self, record: MaterializationRecord) -> None:
        self.materializations.append(record)
        if len(self.materializations) > MAX_MANIFEST_ENTRIES:
            self.materializations = self.materializations[-MAX_MANIFEST_ENTRIES:]

    def to_bytes(self) -> bytes:
        return self.model_dump_json(indent=2).encode("utf-8")


def load_or_create_manifest(
    get_fn,
    manifest_key: str,
    asset: str,
    code_location: str,
    layer: str,
) -> AssetManifest:
    """Load existing manifest from S3 or create a new one.

    Args:
        get_fn: callable(key) -> bytes, raises on missing key
        manifest_key: S3 key for the manifest file
        asset: asset name
        code_location: code location name
        layer: medallion layer
    """
    try:
        data = get_fn(manifest_key)
        return AssetManifest.model_validate_json(data)
    except Exception:
        return AssetManifest(asset=asset, code_location=code_location, layer=layer)


def make_record(
    run_id: str,
    fmt: str,
    count: int,
    size_bytes: int,
    partition: str | None = None,
) -> MaterializationRecord:
    return MaterializationRecord(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        partition=partition,
        format=fmt,
        count=count,
        size_bytes=size_bytes,
    )
