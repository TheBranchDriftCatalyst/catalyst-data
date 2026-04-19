"""S3 Explorer API — browse the Dagster medallion bucket.

GET /viewer/api/s3/list?prefix=&delimiter=/    — list objects/prefixes
GET /viewer/api/s3/read?key=...&max_lines=500  — read file content (JSON/JSONL/text)
GET /viewer/api/s3/stats                       — bucket-level stats
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Query

from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)

router = APIRouter(prefix="/viewer/api/s3", tags=["s3-explorer"])

_client: S3Client | None = None


def _s3() -> S3Client:
    global _client
    if _client is None:
        _client = S3Client(
            endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://minio.minio.svc.cluster.local"),
            access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
            secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
            bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
        )
    return _client


@router.get("/list")
def list_objects(
    prefix: str = Query("", description="S3 key prefix to list"),
    delimiter: str = Query("/", description="Delimiter for grouping (/ for folder-like)"),
    max_keys: int = Query(1000, ge=1, le=5000),
) -> dict[str, Any]:
    """List objects and common prefixes under a given S3 prefix."""
    client = _s3()
    boto = client._client  # noqa: SLF001

    resp = boto.list_objects_v2(
        Bucket=client.bucket,
        Prefix=prefix,
        Delimiter=delimiter,
        MaxKeys=max_keys,
    )

    # Folders (common prefixes)
    folders = []
    for cp in resp.get("CommonPrefixes", []):
        p = cp["Prefix"]
        name = p[len(prefix) :].rstrip("/")
        folders.append({"prefix": p, "name": name})

    # Files
    files = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        # Skip the prefix itself (S3 sometimes returns it)
        if key == prefix:
            continue
        name = key[len(prefix) :]
        files.append(
            {
                "key": key,
                "name": name,
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            }
        )

    # Sort: folders first, then files
    folders.sort(key=lambda f: f["name"])
    files.sort(key=lambda f: f["name"])

    return {
        "prefix": prefix,
        "folders": folders,
        "files": files,
        "truncated": resp.get("IsTruncated", False),
    }


@router.get("/read")
def read_object(
    key: str = Query(..., description="S3 object key to read"),
    max_lines: int = Query(500, ge=1, le=10000, description="Max lines for JSONL"),
) -> dict[str, Any]:
    """Read an S3 object and return its parsed content."""
    client = _s3()
    boto = client._client  # noqa: SLF001

    # Get object metadata first
    try:
        head = boto.head_object(Bucket=client.bucket, Key=key)
    except Exception as e:
        return {"error": f"Object not found: {key}", "detail": str(e)}

    size = head["ContentLength"]
    content_type = head.get("ContentType", "application/octet-stream")

    # Don't read huge files
    if size > 50 * 1024 * 1024:  # 50MB limit
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
        rows = []
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

    elif ext in ("txt", "md", "yaml", "yml", "toml", "cfg", "ini", "prompt"):
        text = raw.decode("utf-8", errors="replace")
        if len(text) > 100_000:
            result["data"] = text[:100_000]
            result["truncated"] = True
        else:
            result["data"] = text

    else:
        # Binary or unknown — just show metadata
        result["data"] = None
        result["preview"] = f"<binary file, {size} bytes, type={content_type}>"

    return result


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
        # Count objects under each prefix
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

    return {
        "bucket": client.bucket,
        "top_level_prefixes": prefixes,
    }
