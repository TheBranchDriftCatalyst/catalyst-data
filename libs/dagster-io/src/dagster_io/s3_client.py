"""Thin boto3 wrapper configured for MinIO."""

from __future__ import annotations

import os
from typing import NamedTuple

import boto3
from botocore.config import Config

from dagster_io.logging import get_logger
from dagster_io.metrics import (
    S3_BYTES_TRANSFERRED,
    S3_OPERATION_DURATION,
    S3_OPERATIONS,
    track_duration,
)

logger = get_logger(__name__)


# Default MinIO config for local dev (Tilt + `task dev`). The seed,
# bench, and migration scripts all need the same fallbacks — keep them
# in ONE place so a deployment-URL change doesn't require chasing down
# N call sites.
_DEFAULT_ENDPOINT = "http://localhost:9000"
_DEFAULT_ACCESS_KEY = "minio"
_DEFAULT_SECRET_KEY = "minio123"
_DEFAULT_BUCKET = "dagster"


class S3Config(NamedTuple):
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str


def resolve_s3_config() -> S3Config:
    """Resolve the MinIO/S3 connection from environment, with dev defaults.

    Reads ``DAGSTER_S3_ENDPOINT_URL`` / ``_ACCESS_KEY`` / ``_SECRET_KEY``
    / ``_BUCKET`` and falls back to the local-dev MinIO that ``task dev``
    spins up. Used by scripts that need to build an S3Client outside of
    a Dagster resource context (seed, bench, migrations).

    Inside Dagster, the resource config takes precedence — this helper
    only fills in when no override is supplied.
    """
    return S3Config(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", _DEFAULT_ENDPOINT),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", _DEFAULT_ACCESS_KEY),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", _DEFAULT_SECRET_KEY),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", _DEFAULT_BUCKET),
    )


class S3Client:
    @classmethod
    def from_env(cls) -> S3Client:
        """Build an S3Client from environment variables with dev defaults.

        Convenience wrapper around :func:`resolve_s3_config`. Use this in
        scripts (seed, bench, migration) so the four env-var lookups
        happen in one place instead of being copied to every call site.
        """
        cfg = resolve_s3_config()
        return cls(
            endpoint_url=cfg.endpoint_url,
            access_key=cfg.access_key,
            secret_key=cfg.secret_key,
            bucket=cfg.bucket,
        )

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(signature_version="s3v4"),
        )
        self.bucket = bucket

    def put_object(self, key: str, data: bytes) -> None:
        logger.debug("S3 put_object bucket=%s key=%s size=%d", self.bucket, key, len(data))
        with track_duration(S3_OPERATION_DURATION, {"operation": "put_object", "bucket": self.bucket}):
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
        S3_OPERATIONS.labels(operation="put_object", bucket=self.bucket).inc()
        S3_BYTES_TRANSFERRED.labels(direction="upload", bucket=self.bucket).inc(len(data))
        logger.info("S3 put_object complete key=%s size=%d", key, len(data))

    def put_object_file(self, key: str, file_path: str) -> int:
        """Upload a file to S3 using multipart upload for large files.

        Returns the file size in bytes.
        """
        import os

        size = os.path.getsize(file_path)
        logger.debug("S3 put_object_file bucket=%s key=%s size=%d", self.bucket, key, size)
        with track_duration(
            S3_OPERATION_DURATION,
            {"operation": "put_object_file", "bucket": self.bucket},
        ):
            self._client.upload_file(file_path, self.bucket, key)
        S3_OPERATIONS.labels(operation="put_object_file", bucket=self.bucket).inc()
        S3_BYTES_TRANSFERRED.labels(direction="upload", bucket=self.bucket).inc(size)
        logger.info("S3 put_object_file complete key=%s size=%d", key, size)
        return size

    def get_object(self, key: str) -> bytes:
        logger.debug("S3 get_object bucket=%s key=%s", self.bucket, key)
        with track_duration(S3_OPERATION_DURATION, {"operation": "get_object", "bucket": self.bucket}):
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
            data = resp["Body"].read()
        S3_OPERATIONS.labels(operation="get_object", bucket=self.bucket).inc()
        S3_BYTES_TRANSFERRED.labels(direction="download", bucket=self.bucket).inc(len(data))
        logger.info("S3 get_object complete key=%s size=%d", key, len(data))
        return data

    def copy_object(self, src_key: str, dst_key: str) -> None:
        logger.debug("S3 copy_object bucket=%s src=%s dst=%s", self.bucket, src_key, dst_key)
        with track_duration(S3_OPERATION_DURATION, {"operation": "copy_object", "bucket": self.bucket}):
            self._client.copy_object(
                Bucket=self.bucket,
                CopySource={"Bucket": self.bucket, "Key": src_key},
                Key=dst_key,
            )
        S3_OPERATIONS.labels(operation="copy_object", bucket=self.bucket).inc()
        logger.info("S3 copy_object complete src=%s dst=%s", src_key, dst_key)

    def list_objects(self, prefix: str) -> list[str]:
        logger.debug("S3 list_objects bucket=%s prefix=%s", self.bucket, prefix)
        with track_duration(S3_OPERATION_DURATION, {"operation": "list_objects", "bucket": self.bucket}):
            resp = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        keys = [obj["Key"] for obj in resp.get("Contents", [])]
        S3_OPERATIONS.labels(operation="list_objects", bucket=self.bucket).inc()
        logger.info("S3 list_objects prefix=%s count=%d", prefix, len(keys))
        return keys

    def list_all_objects(self, prefix: str) -> list[str]:
        """Paginated listing that returns all keys under a prefix."""
        logger.debug("S3 list_all_objects bucket=%s prefix=%s", self.bucket, prefix)
        with track_duration(
            S3_OPERATION_DURATION,
            {"operation": "list_all_objects", "bucket": self.bucket},
        ):
            paginator = self._client.get_paginator("list_objects_v2")
            keys: list[str] = []
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
        S3_OPERATIONS.labels(operation="list_all_objects", bucket=self.bucket).inc()
        logger.info("S3 list_all_objects prefix=%s count=%d", prefix, len(keys))
        return keys

    def head_object(self, key: str) -> dict | None:
        logger.debug("S3 head_object bucket=%s key=%s", self.bucket, key)
        try:
            with track_duration(
                S3_OPERATION_DURATION,
                {"operation": "head_object", "bucket": self.bucket},
            ):
                result = self._client.head_object(Bucket=self.bucket, Key=key)
            S3_OPERATIONS.labels(operation="head_object", bucket=self.bucket).inc()
            return result
        except self._client.exceptions.NoSuchKey:
            logger.debug("S3 head_object key=%s not found", key)
            return None
        except Exception:
            logger.debug("S3 head_object key=%s error", key)
            return None

    def delete_object(self, key: str) -> None:
        """Delete a single object. Idempotent — missing keys succeed silently."""
        logger.debug("S3 delete_object bucket=%s key=%s", self.bucket, key)
        with track_duration(S3_OPERATION_DURATION, {"operation": "delete_object", "bucket": self.bucket}):
            self._client.delete_object(Bucket=self.bucket, Key=key)
        S3_OPERATIONS.labels(operation="delete_object", bucket=self.bucket).inc()
        logger.info("S3 delete_object complete key=%s", key)

    def delete_objects(self, keys: list[str]) -> tuple[int, list[dict]]:
        """Batch delete up to any number of objects.

        Returns (deleted_count, errors). Automatically chunks into the
        S3 batch limit of 1000 keys per request.

        Note: MinIO's DeleteObjects implementation requires Content-MD5,
        which botocore doesn't always set automatically. We fall back to
        per-key deletes if a batch fails with MissingContentMD5.
        """
        if not keys:
            return 0, []
        logger.debug("S3 delete_objects bucket=%s count=%d", self.bucket, len(keys))
        deleted = 0
        errors: list[dict] = []
        with track_duration(
            S3_OPERATION_DURATION,
            {"operation": "delete_objects", "bucket": self.bucket},
        ):
            for i in range(0, len(keys), 1000):
                batch = keys[i : i + 1000]
                try:
                    resp = self._client.delete_objects(
                        Bucket=self.bucket,
                        Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                    )
                    batch_errors = resp.get("Errors", []) or []
                    errors.extend(batch_errors)
                    deleted += len(batch) - len(batch_errors)
                except self._client.exceptions.ClientError as e:
                    # MinIO requires Content-MD5 for DeleteObjects; fall back to singles.
                    if "MissingContentMD5" in str(e) or "Content-Md5" in str(e):
                        logger.warning(
                            "S3 delete_objects batch unsupported (MissingContentMD5), falling back to per-key deletes"
                        )
                        for k in batch:
                            try:
                                self._client.delete_object(Bucket=self.bucket, Key=k)
                                deleted += 1
                            except Exception as inner:
                                errors.append(
                                    {
                                        "Key": k,
                                        "Code": type(inner).__name__,
                                        "Message": str(inner),
                                    }
                                )
                    else:
                        raise
        S3_OPERATIONS.labels(operation="delete_objects", bucket=self.bucket).inc()
        logger.info(
            "S3 delete_objects complete deleted=%d errors=%d total=%d",
            deleted,
            len(errors),
            len(keys),
        )
        return deleted, errors
