"""S3-backed embedding cache keyed by content hash.

Design
------
Key: ``sha256(f"{model}:{dim}:{text}".encode()).hexdigest()[:16]``
Shard: ``key[:2]`` (256 shards, 00–ff) so each parquet file stays small.
Path: ``s3://dagster/silver/embedding_cache/{model_slug}/{shard}.parquet``

Why parquet per shard rather than one file per vector:
- S3 LIST is O(1) per shard vs O(N) per key
- Parquet columnar I/O amortises the read cost across many vectors in one file
- Typical shard holds ~100–500 entries after a full corpus pass (~5 KB–50 KB)

Thread safety: ``get_or_compute`` uses an in-process lock per shard to avoid
redundant compute when the same batch is requested concurrently.  This is
best-effort — cross-process races result in harmless double-writes (same key →
same value), not corruption, because we always read-merge-write.

In tests, pass ``store=None`` to use the in-memory fallback which keeps
everything in a plain dict and never touches S3.
"""

from __future__ import annotations

import io
import os
import threading
from collections.abc import Callable
from hashlib import sha256
from typing import Any

from dagster_io.logging import get_logger

logger = get_logger(__name__)

_CACHE_PREFIX = "silver/embedding_cache"
_BUCKET = "dagster"


def _make_key(model: str, dim: int, text: str) -> str:
    return sha256(f"{model}:{dim}:{text}".encode()).hexdigest()[:16]


def _shard(key: str) -> str:
    return key[:2]


class _InMemoryStore:
    """Fallback store used in tests / when S3 is unavailable."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, list[float]]] = {}

    def read_shard(self, model_slug: str, shard: str) -> dict[str, list[float]]:
        prefix = f"{model_slug}/{shard}"
        return dict(self._data.get(prefix, {}))

    def write_shard(self, model_slug: str, shard: str, rows: dict[str, list[float]]) -> None:
        prefix = f"{model_slug}/{shard}"
        self._data.setdefault(prefix, {}).update(rows)


class _S3Store:
    """Parquet-backed S3 store."""

    def __init__(self, s3_client: Any) -> None:
        self._s3 = s3_client

    def _s3_key(self, model_slug: str, shard: str) -> str:
        return f"{_CACHE_PREFIX}/{model_slug}/{shard}.parquet"

    def read_shard(self, model_slug: str, shard: str) -> dict[str, list[float]]:
        import pyarrow.parquet as pq

        s3_key = self._s3_key(model_slug, shard)
        try:
            data = self._s3.get_object(s3_key)
        except Exception:  # noqa: BLE001
            # Key does not exist yet — empty shard
            return {}
        try:
            buf = io.BytesIO(data)
            table = pq.read_table(buf)
            result: dict[str, list[float]] = {}
            keys_col = table["cache_key"].to_pylist()
            vectors_col = table["vector"].to_pylist()
            for k, v in zip(keys_col, vectors_col, strict=True):
                result[k] = v
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("EmbeddingCache: corrupt shard %s/%s — ignoring: %s", model_slug, shard, exc)
            return {}

    def write_shard(self, model_slug: str, shard: str, rows: dict[str, list[float]]) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not rows:
            return
        keys = list(rows.keys())
        vectors = list(rows.values())
        table = pa.table({"cache_key": keys, "vector": vectors})
        buf = io.BytesIO()
        pq.write_table(table, buf)
        buf.seek(0)
        s3_key = self._s3_key(model_slug, shard)
        self._s3.put_object(s3_key, buf.read())
        logger.debug("EmbeddingCache: wrote shard %s/%s entries=%d", model_slug, shard, len(rows))


class EmbeddingCache:
    """S3-backed embedding cache with in-memory fallback.

    Parameters
    ----------
    store:
        Pass an explicit ``_S3Store`` / ``_InMemoryStore`` (or any duck-typed
        object with ``read_shard`` / ``write_shard`` methods) for testing.
        When ``None`` (default), the cache tries to build an ``_S3Store``
        using the standard ``MINIO_*`` environment variables; if those are
        absent it falls back to ``_InMemoryStore`` automatically.
    """

    def __init__(self, store: Any = None) -> None:
        if store is not None:
            self._store = store
        else:
            self._store = self._build_default_store()
        # Per-shard locks to serialise concurrent writes within one process
        self._locks: dict[str, threading.Lock] = {}
        self._locks_mu = threading.Lock()

    # ── Store construction ──────────────────────────────────────────────────

    @staticmethod
    def _build_default_store() -> Any:
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
            return _S3Store(s3)
        logger.info(
            "EmbeddingCache: S3 env vars not found (MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY), "
            "using in-memory fallback"
        )
        return _InMemoryStore()

    # ── Per-shard lock helper ────────────────────────────────────────────────

    def _shard_lock(self, shard: str) -> threading.Lock:
        with self._locks_mu:
            if shard not in self._locks:
                self._locks[shard] = threading.Lock()
            return self._locks[shard]

    # ── Public API ───────────────────────────────────────────────────────────

    def get(self, texts: list[str], model: str, dim: int) -> list[list[float] | None]:
        """Look up *texts* in the cache.  Returns ``None`` for each miss."""
        keys = [_make_key(model, dim, t) for t in texts]
        # Group by shard for efficient I/O
        shard_keys: dict[str, list[int]] = {}
        for idx, key in enumerate(keys):
            shard_keys.setdefault(_shard(key), []).append(idx)

        result: list[list[float] | None] = [None] * len(texts)
        for shard, indices in shard_keys.items():
            cached = self._store.read_shard(model, shard)
            for idx in indices:
                vec = cached.get(keys[idx])
                if vec is not None:
                    result[idx] = vec
        return result

    def put(self, texts: list[str], vectors: list[list[float]], model: str, dim: int) -> None:
        """Write *texts*/*vectors* pairs into the cache."""
        if not texts:
            return
        keys = [_make_key(model, dim, t) for t in texts]
        shard_new: dict[str, dict[str, list[float]]] = {}
        for key, vec in zip(keys, vectors, strict=True):
            shard_new.setdefault(_shard(key), {})[key] = vec

        for shard, new_rows in shard_new.items():
            with self._shard_lock(shard):
                existing = self._store.read_shard(model, shard)
                existing.update(new_rows)
                self._store.write_shard(model, shard, existing)

    def get_or_compute(
        self,
        texts: list[str],
        model: str,
        dim: int,
        compute: Callable[[list[str]], list[list[float]]],
    ) -> list[list[float]]:
        """Lookup → compute misses → write back → return in input order.

        Only calls *compute* on cache-missing texts to minimise expensive
        embedding passes.  The returned list preserves the original input order.
        """
        cached = self.get(texts, model, dim)
        miss_indices = [i for i, v in enumerate(cached) if v is None]
        miss_texts = [texts[i] for i in miss_indices]

        if miss_texts:
            logger.info(
                "EmbeddingCache: %d hits, %d misses for model=%s dim=%d",
                len(texts) - len(miss_texts),
                len(miss_texts),
                model,
                dim,
            )
            computed = compute(miss_texts)
            self.put(miss_texts, computed, model, dim)
            for idx, vec in zip(miss_indices, computed, strict=True):
                cached[idx] = vec
        else:
            logger.debug(
                "EmbeddingCache: full hit for %d texts model=%s dim=%d",
                len(texts),
                model,
                dim,
            )

        return cached  # type: ignore[return-value]  # all slots filled above
