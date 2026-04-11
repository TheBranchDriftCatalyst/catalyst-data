"""S3 data loader for the media viewer — wraps dagster_io.s3_client.S3Client.

Loads document lists, transcriptions, diarizations, mentions, and assertions
from MinIO following the medallion path conventions established by the IO manager.

Uses a simple TTL cache (dict + timestamps) to avoid hammering S3 on every request.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)

# TTL for cached S3 responses (seconds)
_CACHE_TTL = 60

# ── S3 key patterns (from path_builder conventions) ──────────────────────────
# Unpartitioned:  {layer}/{code_location}/{group}/{asset}/data.{ext}
# Partitioned:    {layer}/{code_location}/{group}/{asset}/{partition_key}/data.{ext}

# Read from env rather than hardcoding — must match dagster_io.path_builder
# which requires DAGSTER_CODE_LOCATION to be set on every pod that uses the
# S3 path helpers. The viewer runs inside the media-ingest code-server pod,
# so this resolves to "media_ingest" in prod.
_CODE_LOCATION = os.environ.get("DAGSTER_CODE_LOCATION", "media_ingest")
_GROUP = "media"

# silver layer — unpartitioned list
DOCUMENTS_KEY = f"silver/{_CODE_LOCATION}/{_GROUP}/media_documents/data.jsonl"

# gold layer — partitioned by document_id
_GOLD_PREFIX = f"gold/{_CODE_LOCATION}/{_GROUP}"
TRANSCRIPTIONS_PREFIX = f"{_GOLD_PREFIX}/media_transcriptions"
DIARIZATIONS_PREFIX = f"{_GOLD_PREFIX}/media_diarization"
MENTIONS_PREFIX = f"{_GOLD_PREFIX}/media_mentions"
ASSERTIONS_PREFIX = f"{_GOLD_PREFIX}/media_assertions"

# NFS media source roots (same as config.py defaults)
_MEDIA_ROOTS: dict[str, str] = {
    "metube": "/data/metube",
    "tubesync": "/data/tubesync",
}


class _CacheEntry:
    __slots__ = ("data", "ts")

    def __init__(self, data: Any) -> None:
        self.data = data
        self.ts = time.monotonic()

    def expired(self) -> bool:
        return (time.monotonic() - self.ts) > _CACHE_TTL


class S3DataService:
    """Loads pipeline output data from S3/MinIO for the viewer API."""

    def __init__(self) -> None:
        self._client: S3Client | None = None
        self._cache: dict[str, _CacheEntry] = {}

    @property
    def client(self) -> S3Client:
        if self._client is None:
            self._client = S3Client(
                endpoint_url=os.environ.get(
                    "DAGSTER_S3_ENDPOINT_URL", "http://minio.minio.svc.cluster.local"
                ),
                access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
                secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
                bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
            )
        return self._client

    # ── cache helpers ────────────────────────────────────────────────────────

    def _get_cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry and not entry.expired():
            return entry.data
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        self._cache[key] = _CacheEntry(data)

    # ── S3 loaders ───────────────────────────────────────────────────────────

    def _load_jsonl(self, s3_key: str) -> list[dict]:
        """Load a JSONL file from S3, returning list of dicts."""
        cached = self._get_cached(s3_key)
        if cached is not None:
            return cached

        try:
            raw = self.client.get_object(s3_key)
            rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            self._set_cached(s3_key, rows)
            return rows
        except Exception:
            logger.exception("Failed to load JSONL from S3 key=%s", s3_key)
            return []

    def _load_json(self, s3_key: str) -> dict | None:
        """Load a single JSON file from S3."""
        cached = self._get_cached(s3_key)
        if cached is not None:
            return cached

        try:
            raw = self.client.get_object(s3_key)
            data = json.loads(raw)
            self._set_cached(s3_key, data)
            return data
        except Exception:
            logger.exception("Failed to load JSON from S3 key=%s", s3_key)
            return None

    # ── public API ───────────────────────────────────────────────────────────

    def list_documents(self) -> list[dict]:
        """Load all media documents from the silver layer."""
        return self._load_jsonl(DOCUMENTS_KEY)

    def get_document(self, document_id: str) -> dict | None:
        """Find a single document by ID."""
        docs = self.list_documents()
        for doc in docs:
            if doc.get("id") == document_id:
                return doc
        return None

    def load_transcription(self, document_id: str) -> dict | None:
        """Load transcription for a single document from the gold layer."""
        key = f"{TRANSCRIPTIONS_PREFIX}/{document_id}/data.json"
        return self._load_json(key)

    def load_diarization(self, document_id: str) -> dict | None:
        """Load diarization (speaker-attributed transcript) for a document."""
        key = f"{DIARIZATIONS_PREFIX}/{document_id}/data.json"
        return self._load_json(key)

    def load_mentions(self, document_id: str) -> list[dict]:
        """Load NER mentions for a document from the gold layer."""
        key = f"{MENTIONS_PREFIX}/{document_id}/data.jsonl"
        return self._load_jsonl(key)

    def load_assertions(self, document_id: str) -> list[dict]:
        """Load qualified assertions for a document from the gold layer."""
        key = f"{ASSERTIONS_PREFIX}/{document_id}/data.jsonl"
        return self._load_jsonl(key)

    def resolve_media_url(self, source_path: str) -> str | None:
        """Convert an NFS source_path to a /viewer/media/ URL.

        Example:
            /data/metube/video.mp4  ->  /viewer/media/metube/video.mp4
            /data/tubesync/a/b.mkv  ->  /viewer/media/tubesync/a/b.mkv
        """
        for source, root in _MEDIA_ROOTS.items():
            if source_path.startswith(root):
                relative = source_path[len(root) :].lstrip("/")
                return f"/viewer/media/{source}/{relative}"
        return None
