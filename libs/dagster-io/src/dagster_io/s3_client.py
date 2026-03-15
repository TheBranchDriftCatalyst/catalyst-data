"""Thin boto3 wrapper configured for MinIO."""

from __future__ import annotations

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


class S3Client:
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
        with track_duration(S3_OPERATION_DURATION, {"operation": "list_all_objects", "bucket": self.bucket}):
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
            with track_duration(S3_OPERATION_DURATION, {"operation": "head_object", "bucket": self.bucket}):
                result = self._client.head_object(Bucket=self.bucket, Key=key)
            S3_OPERATIONS.labels(operation="head_object", bucket=self.bucket).inc()
            return result
        except self._client.exceptions.NoSuchKey:
            logger.debug("S3 head_object key=%s not found", key)
            return None
        except Exception:
            logger.debug("S3 head_object key=%s error", key)
            return None
