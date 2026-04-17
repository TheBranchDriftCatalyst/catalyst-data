"""Watermark state management for incremental API pulls.

Stores JSON blobs in S3 to track the last successful pull timestamp.
Bills use dual watermarks (updateDate + updateDateIncludingText) because
the congress.gov API's updateDate excludes text version updates.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)


@dataclass
class Watermark:
    """State blob for incremental pull tracking."""

    last_update_date: datetime | None = None
    last_update_date_including_text: datetime | None = None
    last_success_run_id: str | None = None
    last_run_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "last_update_date": self.last_update_date.isoformat() if self.last_update_date else None,
            "last_update_date_including_text": (
                self.last_update_date_including_text.isoformat() if self.last_update_date_including_text else None
            ),
            "last_success_run_id": self.last_success_run_id,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Watermark:
        def _parse(v: str | None) -> datetime | None:
            if not v:
                return None
            return datetime.fromisoformat(v)

        return cls(
            last_update_date=_parse(data.get("last_update_date")),
            last_update_date_including_text=_parse(data.get("last_update_date_including_text")),
            last_success_run_id=data.get("last_success_run_id"),
            last_run_at=_parse(data.get("last_run_at")),
        )


class WatermarkManager:
    """Read/write watermark state blobs in S3.

    On first run (blob missing) returns an empty Watermark to trigger full pull.
    Only writes on explicit save — no partial state.
    """

    def __init__(
        self,
        s3_prefix: str = "silver/congress_data/state",
        client: S3Client | None = None,
    ):
        self.s3_prefix = s3_prefix.rstrip("/")
        self._client = client

    @property
    def client(self) -> S3Client:
        if self._client is None:
            self._client = S3Client(
                endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://minio.minio.svc.cluster.local"),
                access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
                secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
                bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
            )
        return self._client

    def _key(self, name: str) -> str:
        return f"{self.s3_prefix}/{name}"

    def load(self, name: str) -> Watermark:
        """Load watermark from S3. Returns empty Watermark if not found (cold start)."""
        key = self._key(name)
        try:
            payload = self.client.get_object(key)
            data = json.loads(payload.decode("utf-8"))
            wm = Watermark.from_dict(data)
            logger.info("Loaded watermark %s: last_update_date=%s", name, wm.last_update_date)
            return wm
        except Exception as e:
            logger.info("No existing watermark at %s (cold start): %s", key, e)
            return Watermark()

    def save(self, name: str, watermark: Watermark, run_id: str | None = None) -> None:
        """Save watermark to S3. Call only on successful completion."""
        watermark.last_run_at = datetime.now(UTC)
        if run_id:
            watermark.last_success_run_id = run_id
        key = self._key(name)
        payload = json.dumps(watermark.to_dict(), indent=2).encode("utf-8")
        self.client.put_object(key, payload)
        logger.info("Saved watermark %s: %s", name, watermark.to_dict())
