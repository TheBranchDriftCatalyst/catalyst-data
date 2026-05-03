"""Append-only IO manager for event-sourced partitioned assets.

Writes each materialization as a new JSONL file in S3 under:
  {layer}/{code_location}/{group}/{asset_key}/{partition_key}/events-{run_id}.jsonl

Reader globs all event files for a partition and dedupes by
configurable natural-key fields.

Used by: bill_actions, bill_cosponsors, member_sponsored, member_cosponsored.
"""

from __future__ import annotations

import json
import typing
from datetime import UTC, datetime

from dagster import ConfigurableIOManager, InputContext, OutputContext
from pydantic import PrivateAttr

from dagster_io.logging import get_logger
from dagster_io.path_builder import build_output_prefix
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)


class AppendIOManager(ConfigurableIOManager):
    """S3-backed append-only IO manager for event-sourced data.

    Each handle_output writes a new file — never overwrites prior data.
    load_input globs all event files for the partition and dedupes.
    """

    endpoint_url: str = "http://minio.minio.svc.cluster.local"
    access_key: str = "minio"
    secret_key: str = "minio123"
    bucket: str = "dagster"

    _client: S3Client | None = PrivateAttr(default=None)

    @property
    def client(self) -> S3Client:
        if self._client is None:
            self._client = S3Client(
                endpoint_url=self.endpoint_url,
                access_key=self.access_key,
                secret_key=self.secret_key,
                bucket=self.bucket,
            )
        return self._client

    def handle_output(self, context: OutputContext, obj: typing.Any) -> None:
        if obj is None:
            context.log.warning("AppendIOManager: skipping — output is None")
            return

        # Build the base prefix from asset metadata + partition
        prefix = build_output_prefix(context)
        run_id = context.run_id
        key = f"{prefix}/events-{run_id}.jsonl"

        # Serialize: list of Pydantic models or dicts → JSONL
        lines = []
        for item in obj:
            if hasattr(item, "model_dump"):
                lines.append(json.dumps(item.model_dump(), default=str))
            elif isinstance(item, dict):
                lines.append(json.dumps(item, default=str))
            else:
                lines.append(json.dumps(item, default=str))

        payload = ("\n".join(lines) + "\n").encode("utf-8")
        self.client.put_object(key, payload)

        context.log.info(f"AppendIOManager: wrote {len(lines)} events to {key}")
        logger.info(
            "AppendIOManager: wrote %d events to %s (%d bytes)",
            len(lines),
            key,
            len(payload),
        )

        # Add metadata for Dagster UI
        context.add_output_metadata(
            {
                "s3_key": key,
                "event_count": len(lines),
                "size_bytes": len(payload),
                "append_timestamp": datetime.now(UTC).isoformat(),
            }
        )

    def load_input(self, context: InputContext) -> list[dict]:
        """Glob all event files for a partition, parse, and dedupe."""
        # Reconstruct the prefix for this asset+partition
        # We need to match the output path structure
        from dagster_io.path_builder import build_input_prefix

        prefix = build_input_prefix(context)

        # List all events-*.jsonl files under this prefix
        all_keys = self.client.list_all_objects(f"{prefix}/")
        event_keys = [k for k in all_keys if k.endswith(".jsonl") and "/events-" in k]

        if not event_keys:
            logger.info("AppendIOManager: no event files at prefix %s", prefix)
            return []

        # Read and parse all events
        all_events: list[dict] = []
        for key in sorted(event_keys):
            try:
                payload = self.client.get_object(key)
                for line in payload.decode("utf-8").strip().split("\n"):
                    if line.strip():
                        all_events.append(json.loads(line))
            except Exception as e:
                logger.warning("AppendIOManager: failed to read %s: %s", key, e)

        # Dedupe by 'id' field if present (our entities all have content-hash IDs)
        if all_events and "id" in all_events[0]:
            seen: set[str] = set()
            deduped: list[dict] = []
            for event in all_events:
                eid = event.get("id", "")
                if eid not in seen:
                    seen.add(eid)
                    deduped.append(event)
            logger.info(
                "AppendIOManager: loaded %d events, deduped to %d from %d files at %s",
                len(all_events),
                len(deduped),
                len(event_keys),
                prefix,
            )
            return deduped

        logger.info(
            "AppendIOManager: loaded %d events from %d files at %s (no dedup key)",
            len(all_events),
            len(event_keys),
            prefix,
        )
        return all_events
