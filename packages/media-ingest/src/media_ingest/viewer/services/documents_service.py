"""Generic document-list loader for any code-location.

The viewer fronts three domains today (media-ingest, congress-data,
open-leaks) and each emits the same `Document` JSONL shape — `id`, `title`,
`source`, `source_path`, `domain`, `metadata` — under
``silver/<code_location>/<group>/<asset>/data.jsonl``. This service captures
that pattern once so each domain just needs a `(code_location, group, asset)`
tuple instead of a copy of the loader.

Wired up through ``viewer/routes/documents_factory.py``, which mounts a
sibling ``/viewer/api/<domain>/documents`` router per registered domain.
Domain-specific extras (media's transcription/diarization/chunks endpoints)
still live in their own routers — this is *only* the generic list+detail
machinery.
"""

from __future__ import annotations

import json
import os
import time
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


class DocumentsService:
    """Generic loader for ``silver/<code_location>/<group>/<asset>/data.jsonl``.

    Lazy S3 client + 60s TTL cache. ``code_location`` matches the Dagster
    code location name (e.g. ``congress_data``, not ``congress-data``);
    ``group`` is the asset group; ``asset`` is the silver asset name (e.g.
    ``congress_documents``). The full silver key is computed from those
    three values.
    """

    def __init__(self, code_location: str, group: str, asset: str) -> None:
        self.code_location = code_location
        self.group = group
        self.asset = asset
        self.silver_key = f"silver/{code_location}/{group}/{asset}/data.jsonl"
        self._client: S3Client | None = None
        self._cache: dict[str, _CacheEntry] = {}

    @property
    def client(self) -> S3Client:
        if self._client is None:
            self._client = _build_client()
        return self._client

    def list_documents(self) -> list[dict[str, Any]]:
        cached = self._cache.get(self.silver_key)
        if cached and not cached.expired():
            return cached.data
        try:
            raw = self.client.get_object(self.silver_key)
            rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        except Exception:
            logger.exception("Failed to load %s docs from %s", self.code_location, self.silver_key)
            return []
        self._cache[self.silver_key] = _CacheEntry(rows)
        return rows

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        for doc in self.list_documents():
            if doc.get("id") == document_id:
                return doc
        return None
