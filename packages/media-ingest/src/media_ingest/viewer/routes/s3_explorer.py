"""S3 Explorer API — browse the Dagster medallion bucket.

Read-only endpoints powering the viewer's S3 explorer:

- ``GET /viewer/api/s3/list``    — list folders/files at a prefix (paginated, full coverage)
- ``GET /viewer/api/s3/index``   — flat recursive key index for a prefix (TTL-cached)
- ``GET /viewer/api/s3/search``  — fzf-style fuzzy search with match indices
- ``GET /viewer/api/s3/read``    — read & parse JSON/JSONL/text content
- ``GET /viewer/api/s3/raw``     — stream raw bytes (image/audio/video/download)
- ``GET /viewer/api/s3/stats``   — top-level prefix stats
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)

router = APIRouter(prefix="/viewer/api/s3", tags=["s3-explorer"])

_client: S3Client | None = None

# In-process cache for the recursive key index. Keyed by prefix.
_INDEX_TTL_SECONDS = 60.0
_index_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# In-process cache for aggregated folder stats. Keyed by prefix.
_STATS_TTL_SECONDS = 120.0
_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_stats_inflight: dict[str, threading.Thread] = {}
_stats_lock = threading.Lock()


def _s3() -> S3Client:
    """Return the S3 client. Same backend (MinIO) in dev and prod —
    dev points at a local container at ``localhost:9000`` (see the
    Tilt-managed ``minio`` resource), prod points at the cluster
    MinIO Tenant via Tilt's port-forward."""
    global _client
    if _client is None:
        _client = S3Client(
            endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
            access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
            secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
            bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
        )
    return _client


# ── fzf-style fuzzy scorer ──────────────────────────────────────────────────
#
# Subsequence match with bonuses for word-start, segment-boundary, exact
# substring, and consecutive runs. Returns (score, match_indices) or None
# if no match. Indices are positions in the haystack so the UI can highlight.

_BOUNDARY_CHARS = frozenset("/_.-")


def _fuzzy_score(query: str, haystack: str) -> tuple[int, list[int]] | None:
    if not query:
        return (0, [])
    q = query.lower()
    h = haystack.lower()

    # Cheap exact-substring fast path — also boosts ranking heavily.
    sub = h.find(q)
    if sub >= 0:
        indices = list(range(sub, sub + len(q)))
        # Big base score for substring; bonus if at a segment boundary.
        score = 1000 + len(q) * 10
        if sub == 0 or h[sub - 1] in _BOUNDARY_CHARS:
            score += 50
        # Penalize length so shorter haystacks rank higher among substring hits.
        score -= len(haystack)
        return (score, indices)

    # Subsequence match.
    indices: list[int] = []
    qi = 0
    last_match = -2
    score = 0
    for hi, ch in enumerate(h):
        if qi < len(q) and ch == q[qi]:
            indices.append(hi)
            # Consecutive bonus
            if hi == last_match + 1:
                score += 8
            # Word/segment-boundary bonus
            if hi == 0 or h[hi - 1] in _BOUNDARY_CHARS:
                score += 12
            score += 2
            last_match = hi
            qi += 1
    if qi != len(q):
        return None
    # Penalty for total span (prefer tight matches) and overall length.
    span = indices[-1] - indices[0] + 1
    score -= span // 2
    score -= len(haystack) // 8
    return (score, indices)


# ── recursive key index (TTL-cached) ───────────────────────────────────────


def _build_index(prefix: str) -> list[dict[str, Any]]:
    """Recursively list every object under ``prefix`` via paginator."""
    client = _s3()
    boto = client._client  # noqa: SLF001
    paginator = boto.get_paginator("list_objects_v2")
    out: list[dict[str, Any]] = []
    for page in paginator.paginate(Bucket=client.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            out.append(
                {
                    "key": key,
                    "name": key.rsplit("/", 1)[-1],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                }
            )
    return out


def _get_index(prefix: str, refresh: bool = False) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _index_cache.get(prefix)
    if not refresh and cached is not None:
        ts, data = cached
        if now - ts < _INDEX_TTL_SECONDS:
            return data
    data = _build_index(prefix)
    _index_cache[prefix] = (now, data)
    return data


# ── routes ──────────────────────────────────────────────────────────────────


@router.get("/list")
def list_objects(
    prefix: str = Query("", description="S3 key prefix to list"),
    delimiter: str = Query("/", description="Delimiter for grouping (/ for folder-like)"),
    max_keys: int = Query(1000, ge=1, le=5000),
) -> dict[str, Any]:
    """List objects and common prefixes under a given S3 prefix.

    Paginates through every page so callers see the full set under the
    delimiter — the previous single-page implementation silently capped
    very wide prefixes at ``max_keys``. Stats (sizes, counts) are *not*
    included here — the explorer fetches them separately via
    ``/folder_stats`` so the listing renders without waiting for the
    recursive aggregation.
    """
    client = _s3()
    boto = client._client  # noqa: SLF001
    paginator = boto.get_paginator("list_objects_v2")

    folders: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    seen_prefixes: set[str] = set()
    truncated = False
    total_seen = 0

    for page in paginator.paginate(
        Bucket=client.bucket,
        Prefix=prefix,
        Delimiter=delimiter,
        PaginationConfig={"PageSize": min(max_keys, 1000)},
    ):
        for cp in page.get("CommonPrefixes") or []:
            p = cp["Prefix"]
            if p in seen_prefixes:
                continue
            seen_prefixes.add(p)
            folders.append({"prefix": p, "name": p[len(prefix) :].rstrip("/")})

        for obj in page.get("Contents") or []:
            key = obj["Key"]
            if key == prefix:
                continue
            files.append(
                {
                    "key": key,
                    "name": key[len(prefix) :],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                }
            )

        total_seen += page.get("KeyCount", 0)
        if total_seen >= max_keys:
            truncated = bool(page.get("IsTruncated"))
            break

    folders.sort(key=lambda f: f["name"])
    files.sort(key=lambda f: f["name"])

    return {
        "prefix": prefix,
        "folders": folders,
        "files": files,
        "truncated": truncated,
    }


def _compute_stats(prefix: str) -> dict[str, Any]:
    """Aggregate per-folder + prefix-level stats from the cached recursive index.

    Bucketize every key under ``prefix`` by its first path segment. Each
    immediate subfolder gets ``total_size``, ``file_count``, ``last_modified``;
    the overall prefix gets the same plus ``folder_count``.
    """
    keys = _get_index(prefix, refresh=False)
    folder_acc: dict[str, dict[str, Any]] = {}
    total_size = 0
    last_modified = ""
    immediate_files = 0
    for entry in keys:
        size = int(entry.get("size", 0) or 0)
        lm = entry.get("last_modified", "") or ""
        total_size += size
        if lm > last_modified:
            last_modified = lm
        rel = entry["key"][len(prefix) :]
        if "/" in rel:
            first = rel.split("/", 1)[0]
            fp = prefix + first + "/"
            acc = folder_acc.setdefault(fp, {"total_size": 0, "file_count": 0, "last_modified": ""})
            acc["total_size"] += size
            acc["file_count"] += 1
            if lm > acc["last_modified"]:
                acc["last_modified"] = lm
        else:
            immediate_files += 1
    return {
        "folder_stats": folder_acc,
        "prefix_stats": {
            "total_size": total_size,
            "file_count": len(keys),
            "folder_count": len(folder_acc),
            "immediate_files": immediate_files,
            "last_modified": last_modified,
        },
    }


def _stats_worker(prefix: str) -> None:
    """Background worker — computes stats and parks them in the cache."""
    try:
        stats = _compute_stats(prefix)
        with _stats_lock:
            _stats_cache[prefix] = (time.monotonic(), stats)
    except Exception:
        logger.exception("Folder stats compute failed for prefix=%r", prefix)
    finally:
        with _stats_lock:
            _stats_inflight.pop(prefix, None)


@router.get("/folder_stats")
def folder_stats(
    prefix: str = Query("", description="S3 prefix to aggregate stats under"),
    refresh: bool = Query(False, description="Bust cache and recompute"),
) -> dict[str, Any]:
    """Non-blocking folder stats endpoint.

    On cache hit returns ``status: "ready"`` with the aggregates. On miss
    spawns a background thread to compute, returns ``status: "computing"``
    immediately, and the client polls until the cache fills. The cache TTL
    is wider than the index TTL because stats are expensive and don't need
    to feel real-time.
    """
    now = time.monotonic()
    with _stats_lock:
        cached = _stats_cache.get(prefix)
        cache_fresh = cached is not None and now - cached[0] < _STATS_TTL_SECONDS
        if cache_fresh and not refresh:
            ts, stats = cached  # type: ignore[misc]
            return {"prefix": prefix, "status": "ready", "age_seconds": now - ts, **stats}
        if refresh:
            _stats_cache.pop(prefix, None)
        if prefix not in _stats_inflight:
            t = threading.Thread(target=_stats_worker, args=(prefix,), daemon=True)
            _stats_inflight[prefix] = t
            t.start()
    return {"prefix": prefix, "status": "computing"}


@router.get("/index")
def index_prefix(
    prefix: str = Query("", description="S3 key prefix to index recursively"),
    refresh: bool = Query(False, description="Bust the in-process cache"),
) -> dict[str, Any]:
    """Return a flat list of every object under ``prefix`` (TTL-cached)."""
    keys = _get_index(prefix, refresh=refresh)
    return {"prefix": prefix, "count": len(keys), "keys": keys}


@router.get("/search")
def search_keys(
    q: str = Query(..., min_length=1, description="Fuzzy query"),
    prefix: str = Query("", description="Restrict search to this prefix"),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Fuzzy-search keys under ``prefix`` with fzf-style scoring.

    Match indices are positions in the **full key** so the UI can highlight
    matched chars without re-scoring on the client.
    """
    keys = _get_index(prefix, refresh=False)
    hits: list[dict[str, Any]] = []
    for entry in keys:
        scored = _fuzzy_score(q, entry["key"])
        if scored is None:
            continue
        score, match_indices = scored
        hits.append({**entry, "score": score, "match_indices": match_indices})
    hits.sort(key=lambda h: (-h["score"], h["key"]))
    return {"q": q, "prefix": prefix, "total": len(hits), "hits": hits[:limit]}


@router.get("/read")
def read_object(
    key: str = Query(..., description="S3 object key to read"),
    max_lines: int = Query(500, ge=1, le=10000, description="Max lines for JSONL"),
) -> dict[str, Any]:
    """Read an S3 object and return its parsed content."""
    client = _s3()
    boto = client._client  # noqa: SLF001

    try:
        head = boto.head_object(Bucket=client.bucket, Key=key)
    except Exception as e:
        return {"error": f"Object not found: {key}", "detail": str(e)}

    size = head["ContentLength"]
    content_type = head.get("ContentType", "application/octet-stream")

    if size > 50 * 1024 * 1024:
        return {
            "key": key,
            "size": size,
            "content_type": content_type,
            "error": f"File too large to preview ({size / (1024 * 1024):.1f} MB)",
            "preview": None,
        }

    raw = client.get_object(key)
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""

    result: dict[str, Any] = {
        "key": key,
        "size": size,
        "content_type": content_type,
        "format": ext,
    }

    if ext == "jsonl":
        lines = raw.decode("utf-8", errors="replace").strip().split("\n")
        total_lines = len(lines)
        truncated = total_lines > max_lines
        rows: list[Any] = []
        for line in lines[:max_lines]:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"_raw": line})
        result["data"] = rows
        result["total_lines"] = total_lines
        result["truncated"] = truncated

    elif ext == "json":
        try:
            result["data"] = json.loads(raw)
        except json.JSONDecodeError:
            result["data"] = None
            result["error"] = "Invalid JSON"

    elif ext in ("pkl", "pickle"):
        result["data"] = None
        result["preview"] = f"<binary pickle file, {size} bytes>"

    elif ext in ("txt", "md", "yaml", "yml", "toml", "cfg", "ini", "prompt", "log", "csv", "tsv"):
        text = raw.decode("utf-8", errors="replace")
        if len(text) > 100_000:
            result["data"] = text[:100_000]
            result["truncated"] = True
        else:
            result["data"] = text

    else:
        result["data"] = None
        result["preview"] = f"<binary file, {size} bytes, type={content_type}>"

    return result


@router.get("/raw")
def stream_object(
    key: str = Query(..., description="S3 object key to stream"),
    download: bool = Query(False, description="Force Content-Disposition: attachment"),
) -> StreamingResponse:
    """Stream raw object bytes — powers inline image/audio/video preview and downloads."""
    client = _s3()
    boto = client._client  # noqa: SLF001
    try:
        head = boto.head_object(Bucket=client.bucket, Key=key)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Object not found: {key}") from e

    content_type = head.get("ContentType", "application/octet-stream")
    size = head["ContentLength"]

    def _iter() -> Iterator[bytes]:
        resp = boto.get_object(Bucket=client.bucket, Key=key)
        body = resp["Body"]
        try:
            yield from body.iter_chunks(chunk_size=64 * 1024)
        finally:
            body.close()

    headers = {
        "Content-Length": str(size),
        "Cache-Control": "private, max-age=60",
    }
    if download:
        filename = key.rsplit("/", 1)[-1] or "download"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return StreamingResponse(_iter(), media_type=content_type, headers=headers)


@router.get("/stats")
def bucket_stats() -> dict[str, Any]:
    """Quick bucket stats — count objects by top-level prefix."""
    client = _s3()
    boto = client._client  # noqa: SLF001

    resp = boto.list_objects_v2(
        Bucket=client.bucket,
        Delimiter="/",
        MaxKeys=100,
    )

    prefixes = []
    for cp in resp.get("CommonPrefixes", []):
        p = cp["Prefix"]
        count_resp = boto.list_objects_v2(
            Bucket=client.bucket,
            Prefix=p,
            MaxKeys=1,
        )
        prefixes.append(
            {
                "prefix": p.rstrip("/"),
                "has_objects": count_resp.get("KeyCount", 0) > 0,
            }
        )

    return {"bucket": client.bucket, "top_level_prefixes": prefixes}


# ── delete endpoints ────────────────────────────────────────────────────────
#
# The S3 explorer is otherwise read-only. Deletes were added intentionally
# for the dev-tool case (clearing fixture clutter, dropping a bad bench run,
# scrubbing a test silver row before re-seeding). MinIO behind the scenes,
# so destruction is bounded to the local bucket; in prod the same routes
# touch the cluster bucket — that's a deliberate dev-ops affordance, NOT a
# safety hole. The frontend gates each click behind a `window.confirm`.


def _bust_caches(prefixes: list[str]) -> None:
    """Clear any cached index/stats entries that overlap the deleted keys.

    Both caches key on prefix; a delete invalidates every cached prefix
    that is an ancestor (since the deleted object would have been in their
    recursive listing). Cheap to walk — typical cache size is < 50 entries.
    """
    affected: set[str] = set()
    for p in prefixes:
        for cached_prefix in list(_index_cache.keys()):
            if p.startswith(cached_prefix) or cached_prefix.startswith(p):
                affected.add(cached_prefix)
        with _stats_lock:
            for cached_prefix in list(_stats_cache.keys()):
                if p.startswith(cached_prefix) or cached_prefix.startswith(p):
                    affected.add(cached_prefix)
    for p in affected:
        _index_cache.pop(p, None)
        with _stats_lock:
            _stats_cache.pop(p, None)


@router.delete("/object")
def delete_object(
    key: str = Query(..., min_length=1, description="S3 object key to delete"),
) -> dict[str, Any]:
    """Delete a single object. Idempotent — missing keys return ``deleted: 0``."""
    client = _s3()
    if key.endswith("/"):
        raise HTTPException(
            status_code=400,
            detail="Use DELETE /prefix for prefix-style (recursive) deletes.",
        )
    # Confirm existence so the caller gets a useful response (delete_object
    # itself is silently idempotent on S3).
    head = client.head_object(key)
    if head is None:
        return {"deleted": 0, "key": key, "missing": True}
    client.delete_object(key)
    _bust_caches([key])
    logger.info("S3 explorer: deleted object key=%s", key)
    return {"deleted": 1, "key": key}


@router.delete("/prefix")
def delete_prefix(
    prefix: str = Query(..., min_length=1, description="S3 prefix to delete recursively"),
    confirm: bool = Query(False, description="Required guard — must be true to proceed (defense in depth)"),
) -> dict[str, Any]:
    """Recursively delete every object under ``prefix``.

    ``confirm=true`` is required to actually delete — without it the route
    returns the would-be victim count so the caller can decide. The frontend
    sends two requests: a dry-run (``confirm=false``) to populate the
    confirmation dialog, then the live one.
    """
    if not prefix.endswith("/"):
        # Guard against accidentally deleting a single object via this route.
        raise HTTPException(
            status_code=400,
            detail="Prefix must end with '/'. Use DELETE /object for single keys.",
        )
    if prefix == "/":
        # Refuse to nuke the entire bucket.
        raise HTTPException(status_code=400, detail="Refusing to delete bucket root.")
    client = _s3()
    keys = client.list_all_objects(prefix)
    if not confirm:
        return {"deleted": 0, "prefix": prefix, "would_delete": len(keys), "dry_run": True}
    if not keys:
        return {"deleted": 0, "prefix": prefix, "missing": True}
    deleted, errors = client.delete_objects(keys)
    _bust_caches([prefix])
    logger.info("S3 explorer: deleted prefix=%s deleted=%d errors=%d", prefix, deleted, len(errors))
    return {
        "deleted": deleted,
        "prefix": prefix,
        "errors": errors,
    }
