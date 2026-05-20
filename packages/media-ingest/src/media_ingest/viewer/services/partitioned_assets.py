"""Loader for partitioned gold/silver/platinum assets.

A bill, a media file, a leak document — anything Dagster materialises
per-partition lands at::

    <layer>/<code_location>/<group>/<asset>/<partition>/data.<ext>

This service generalises the per-partition fetch so the per-domain
viewer routes can expose them through a uniform URL shape::

    /viewer/api/<domain>/bills/<partition>/<asset_label>

Auto-detects the on-disk format (``.jsonl`` → list-of-rows, ``.json`` →
single object, ``events-*.jsonl`` → concatenated event-stream from the
``LocalAppendIOManager``-style writer used by the structured-assertion
pipeline).

60-second per-partition cache so a tab strip in the UI that fetches
detail + assertions + chunks in parallel doesn't slam MinIO.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from typing import Any

from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)

_CACHE_TTL = 60.0


class _CacheEntry:
    __slots__ = ("data", "ts")

    def __init__(self, data: Any) -> None:
        self.data = data
        self.ts = time.monotonic()

    def expired(self) -> bool:
        return (time.monotonic() - self.ts) > _CACHE_TTL


def _build_client() -> S3Client:
    return S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )


class PartitionedAssetService:
    """Load + list partitions for one Dagster asset.

    Args:
        layer: ``bronze`` | ``silver`` | ``gold`` | ``platinum``
        code_location: Dagster code-location name (snake_case — e.g.
            ``congress_data``, ``media_ingest``).
        group: Asset group name (e.g. ``bill``, ``media``,
            ``congress``).
        asset: Dagster asset name (e.g. ``bill_document``,
            ``bill_assertions``, ``congress_structured_assertions``).
        format: ``jsonl`` | ``json`` | ``events`` — the on-disk shape.
            ``events`` reads ``events-<run_id>.jsonl`` files and
            concatenates them (one row per line, all files merged).
    """

    def __init__(
        self,
        *,
        layer: str,
        code_location: str,
        group: str,
        asset: str,
        format: str = "jsonl",
    ) -> None:
        self.layer = layer
        self.code_location = code_location
        self.group = group
        self.asset = asset
        self.format = format
        self.prefix = f"{layer}/{code_location}/{group}/{asset}/"
        self._client: S3Client | None = None
        self._partitions_cache: _CacheEntry | None = None
        self._payload_cache: dict[str, _CacheEntry] = {}

    @property
    def client(self) -> S3Client:
        if self._client is None:
            self._client = _build_client()
        return self._client

    # ── Partition discovery ────────────────────────────────────────

    def list_partitions(self) -> list[str]:
        """Return partition keys (first path segment under prefix)."""
        if self._partitions_cache and not self._partitions_cache.expired():
            return self._partitions_cache.data
        try:
            keys = self._list_keys(self.prefix)
        except Exception:
            logger.exception("Failed to list partitions for %s", self.prefix)
            return []
        partitions: set[str] = set()
        for key in keys:
            # Strip the prefix; next segment up to the first '/' is the partition.
            rel = key[len(self.prefix) :]
            if not rel or rel.startswith("_"):
                continue  # _manifest.json etc.
            seg = rel.split("/", 1)[0]
            if seg and not seg.startswith("_"):
                partitions.add(seg)
        result = sorted(partitions)
        self._partitions_cache = _CacheEntry(result)
        return result

    # ── Payload loading ────────────────────────────────────────────

    def load(self, partition: str) -> Any:
        """Return the parsed payload for one partition.

        - ``jsonl``: list[dict]
        - ``json``: dict
        - ``events``: list[dict] — concatenation of all events-*.jsonl
          files under the partition, in lexicographic order.

        Returns ``None`` (or empty list, for jsonl/events) when the
        partition directory exists but the data file is missing.
        """
        cached = self._payload_cache.get(partition)
        if cached and not cached.expired():
            return cached.data
        try:
            payload = self._load_uncached(partition)
        except Exception:
            logger.exception(
                "Failed to load %s for partition %r",
                self.prefix,
                partition,
            )
            payload = None if self.format == "json" else []
        self._payload_cache[partition] = _CacheEntry(payload)
        return payload

    def _load_uncached(self, partition: str) -> Any:
        base = f"{self.prefix}{partition}/"
        if self.format == "json":
            key = f"{base}data.json"
            return json.loads(self.client.get_object(key).decode("utf-8"))
        if self.format == "jsonl":
            key = f"{base}data.jsonl"
            return self._read_jsonl(key)
        if self.format == "events":
            return self._read_events(base)
        raise ValueError(f"Unknown format: {self.format!r}")

    def _read_jsonl(self, key: str) -> list[dict]:
        raw = self.client.get_object(key).decode("utf-8")
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    def _read_events(self, base: str) -> list[dict]:
        keys = sorted(k for k in self._list_keys(base) if k[len(base) :].startswith("events-"))
        rows: list[dict] = []
        for key in keys:
            rows.extend(self._read_jsonl(key))
        return rows

    # ── S3 listing helper ─────────────────────────────────────────

    def _list_keys(self, prefix: str) -> Iterable[str]:
        # list_all_objects paginates — important for buckets that grow
        # past the 1000-key list_objects_v2 page limit (a busy congress
        # corpus or long event stream can hit that fast).
        return self.client.list_all_objects(prefix)
