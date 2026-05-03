"""S3-backed cluster cache keyed by content hash.

Design
------
Key: ``sha256(doc_text + ner_model + str(threshold) + str(proximity_radius)).hexdigest()[:16]``
Path: ``s3://dagster/silver/<code_loc>/cluster_cache/<key>.json``

Unlike the embedding cache (parquet shards for large vectors), clusters are
small (~few KB per doc), so we store one JSON blob per cache entry.  The
sharding scheme uses the first 2 hex chars of the key (256 shards) to avoid
exploding the S3 namespace while keeping LIST calls O(1).

Thread safety: ``get_or_compute`` uses an in-process lock per key to avoid
redundant NER+cluster compute when two threads request the same doc
simultaneously.  Cross-process races result in harmless double-writes (same
key → same value), not corruption.

In tests, pass ``store=None`` to use the in-memory fallback which keeps
everything in a plain dict and never touches S3.  This mirrors the
``EmbeddingCache`` pattern from Phase 1 (CD-zt85).

Phase 3 (CD-80ic).
"""

from __future__ import annotations

import json
import os
import threading
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from dagster_io.logging import get_logger

if TYPE_CHECKING:
    from catalyst_exgraph.state import EntityCluster

logger = get_logger(__name__)

_CACHE_PREFIX = "silver"
_BUCKET = "dagster"


def _make_cluster_key(doc_text: str, ner_model: str, params: dict) -> str:
    """Deterministic cache key for a (doc, ner_model, params) triple."""
    threshold = str(params.get("threshold", ""))
    proximity = str(params.get("proximity_radius", ""))
    payload = f"{doc_text}\x00{ner_model}\x00{threshold}\x00{proximity}"
    return sha256(payload.encode()).hexdigest()[:16]


def _shard(key: str) -> str:
    return key[:2]


class _InMemoryStore:
    """Fallback store used in tests / when S3 is unavailable."""

    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = {}

    def read(self, code_loc: str, key: str) -> list[dict] | None:
        return self._data.get(f"{code_loc}/{key}")

    def write(self, code_loc: str, key: str, clusters: list[dict]) -> None:
        self._data[f"{code_loc}/{key}"] = clusters


class _S3Store:
    """JSON-backed S3 store."""

    def __init__(self, s3_client: Any, code_loc: str) -> None:
        self._s3 = s3_client
        self._code_loc = code_loc

    def _s3_key(self, key: str) -> str:
        shard = _shard(key)
        return f"{_CACHE_PREFIX}/{self._code_loc}/cluster_cache/{shard}/{key}.json"

    def read(self, _code_loc: str, key: str) -> list[dict] | None:
        s3_key = self._s3_key(key)
        try:
            data = self._s3.get_object(s3_key)
        except Exception:  # noqa: BLE001
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ClusterCache: corrupt entry %s — ignoring: %s", s3_key, exc)
            return None

    def write(self, _code_loc: str, key: str, clusters: list[dict]) -> None:
        s3_key = self._s3_key(key)
        try:
            self._s3.put_object(s3_key, json.dumps(clusters).encode("utf-8"))
            logger.debug("ClusterCache: wrote %s (%d clusters)", s3_key, len(clusters))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ClusterCache: write failed for %s: %s", s3_key, exc)


class ClusterCache:
    """S3-backed cluster cache with in-memory fallback.

    Parameters
    ----------
    store:
        Pass an explicit ``_S3Store`` / ``_InMemoryStore`` (or any duck-typed
        object with ``read`` / ``write`` methods) for testing.
        When ``None`` (default), the cache tries to build an ``_S3Store``
        using the standard ``MINIO_*`` / ``AWS_*`` environment variables;
        if those are absent it falls back to ``_InMemoryStore`` automatically.
    code_location:
        The Dagster code location string — used as part of the S3 key prefix
        so different deployments don't share cluster caches.  Defaults to
        the ``DAGSTER_CODE_LOCATION`` env var or ``"default"``.
    """

    def __init__(
        self,
        store: Any = None,
        code_location: str | None = None,
    ) -> None:
        self._code_loc = code_location or os.environ.get("DAGSTER_CODE_LOCATION", "default")
        if store is not None:
            self._store = store
        else:
            self._store = self._build_default_store(self._code_loc)
        # Per-key locks to serialise concurrent compute within one process
        self._locks: dict[str, threading.Lock] = {}
        self._locks_mu = threading.Lock()

    # ── Store construction ──────────────────────────────────────────────────

    @staticmethod
    def _build_default_store(code_loc: str) -> Any:
        endpoint = os.environ.get("MINIO_ENDPOINT") or os.environ.get("AWS_ENDPOINT_URL")
        access_key = os.environ.get("MINIO_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID")
        secret_key = os.environ.get("MINIO_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")
        if endpoint and access_key and secret_key:
            from dagster_io.s3_client import S3Client

            s3 = S3Client(
                endpoint_url=endpoint,
                access_key=access_key,
                secret_key=secret_key,
                bucket=_BUCKET,
            )
            return _S3Store(s3, code_loc)
        logger.info(
            "ClusterCache: S3 env vars not found (MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY), "
            "using in-memory fallback"
        )
        return _InMemoryStore()

    # ── Per-key lock helper ─────────────────────────────────────────────────

    def _key_lock(self, key: str) -> threading.Lock:
        with self._locks_mu:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    # ── Public API ──────────────────────────────────────────────────────────

    def get(
        self,
        doc_id: str,
        doc_text: str,
        ner_model: str,
        params: dict,
    ) -> list[EntityCluster] | None:
        """Return cached clusters for the given (doc_text, ner_model, params) triple.

        Returns ``None`` on a cache miss so callers can distinguish "not
        computed yet" from "computed, returned empty list".

        ``doc_id`` is informational only (used in log messages).
        """
        key = _make_cluster_key(doc_text, ner_model, params)
        result = self._store.read(self._code_loc, key)
        if result is None:
            logger.debug("ClusterCache: miss  doc_id=%s model=%s key=%s", doc_id, ner_model, key)
        else:
            logger.debug(
                "ClusterCache: hit   doc_id=%s model=%s key=%s clusters=%d",
                doc_id,
                ner_model,
                key,
                len(result),
            )
        return result

    def put(
        self,
        doc_id: str,
        doc_text: str,
        ner_model: str,
        params: dict,
        clusters: list[EntityCluster],
    ) -> None:
        """Write clusters to the cache.

        Idempotent: writing the same key twice with the same value is a no-op
        from the perspective of correctness (content-addressed key).
        """
        key = _make_cluster_key(doc_text, ner_model, params)
        self._store.write(self._code_loc, key, list(clusters))
        logger.debug(
            "ClusterCache: put   doc_id=%s model=%s key=%s clusters=%d",
            doc_id,
            ner_model,
            key,
            len(clusters),
        )

    def get_or_compute(
        self,
        doc_id: str,
        doc_text: str,
        ner_model: str,
        params: dict,
        compute_fn,
    ) -> list[EntityCluster]:
        """Lookup → compute on miss → write back → return.

        ``compute_fn()`` is called with no arguments only on a cache miss.
        The per-key lock ensures a single process won't compute the same entry
        twice under concurrent load.

        Args:
            doc_id: Informational doc identifier (used in logs).
            doc_text: Full document text (used as cache key input).
            ner_model: NER model name (used as cache key input).
            params: Dict of clustering params, e.g.
                ``{"threshold": 0.85, "proximity_radius": 512}``.
            compute_fn: Zero-argument callable that returns
                ``list[EntityCluster]``.
        """
        key = _make_cluster_key(doc_text, ner_model, params)
        with self._key_lock(key):
            cached = self._store.read(self._code_loc, key)
            if cached is not None:
                logger.debug("ClusterCache: get_or_compute HIT doc_id=%s model=%s", doc_id, ner_model)
                return cached
            logger.info(
                "ClusterCache: get_or_compute MISS doc_id=%s model=%s — computing",
                doc_id,
                ner_model,
            )
            clusters = compute_fn()
            self._store.write(self._code_loc, key, list(clusters))
            return clusters
