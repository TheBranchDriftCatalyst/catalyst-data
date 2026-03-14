"""MinIO-backed IO manager for Dagster with medallion architecture."""

from __future__ import annotations

import hashlib
import json
import os
import typing
from datetime import datetime, timezone

from dagster import ConfigurableIOManager, InputContext, OutputContext
from pydantic import PrivateAttr

from dagster_io.manifest import load_or_create_manifest, make_record
from dagster_io.path_builder import build_asset_root, build_input_prefix, build_output_prefix
from dagster_io.s3_client import S3Client
from dagster_io.serializers import _extract_schema, deserialize, serialize


class MinioIOManager(ConfigurableIOManager):
    """S3-backed IO manager targeting a MinIO instance.

    v2: Medallion layers, Hive-style partitions, overwrite-in-place with
    MinIO bucket versioning, manifest tracking.
    """

    endpoint_url: str = os.environ.get(
        "DAGSTER_S3_ENDPOINT_URL", "http://minio.minio.svc.cluster.local"
    )
    access_key: str = os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio")
    secret_key: str = os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123")
    bucket: str = os.environ.get("DAGSTER_S3_BUCKET", "dagster")

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

    def _get_type_hint(self, context: OutputContext) -> type | None:
        try:
            th = context.dagster_type.typing_type
            if th is typing.Any:
                return None
            return th
        except Exception:
            return None

    def _extract_config_key(self, context: OutputContext) -> str | None:
        """Extract config_key from EmbeddingConfigResource or asset definition metadata.

        Precedence:
        1. ``context.resources.embedding_config`` (runtime resource)
        2. ``context.definition_metadata["config_key"]`` (static asset metadata)
        """
        # Try the EmbeddingConfigResource first (dynamic, set per code location)
        try:
            return context.resources.embedding_config.config_key  # type: ignore[union-attr]
        except (AttributeError, Exception):
            pass
        # Fallback: static metadata on the asset definition
        try:
            return (context.definition_metadata or {}).get("config_key")
        except Exception:
            return None

    def _build_metadata(
        self,
        context: OutputContext,
        fmt: str,
        count: int,
        size_bytes: int,
        type_hint: type | None,
        obj: typing.Any,
        config_key: str | None = None,
    ) -> dict:
        """Build enhanced _metadata.json sidecar content."""
        asset_root = build_asset_root(context, config_key=config_key)
        layer = asset_root.split("/")[0]
        code_location = asset_root.split("/")[1] if "/" in asset_root else "unknown"

        partition = None
        if context.has_asset_partitions:
            partition = str(context.asset_partition_key)

        upstream = []
        try:
            for dep_key in context.asset_key.path:
                pass  # upstream extracted below
            if hasattr(context, "upstream_output") and context.upstream_output:
                upstream = [context.upstream_output.asset_key.to_user_string()]
        except Exception:
            pass

        meta = {
            "format": fmt,
            "type": str(type_hint) if type_hint else "unknown",
            "count": count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "schema": _extract_schema(obj),
            "size_bytes": size_bytes,
            "run_id": context.run_id,
            "code_location": code_location,
            "asset_key": context.asset_key.to_user_string(),
            "partition": partition,
            "layer": layer,
            "upstream_assets": upstream,
        }
        if config_key:
            meta["config_key"] = config_key
        return meta

    def handle_output(self, context: OutputContext, obj: typing.Any) -> None:
        if obj is None:
            context.log.warning("Skipping S3 write — output is None")
            return

        config_key = self._extract_config_key(context)

        prefix = build_output_prefix(context, config_key=config_key)
        type_hint = self._get_type_hint(context)
        payload, ext, ser_meta = serialize(obj, type_hint)

        # Content-hash dedup: skip write if payload unchanged from last materialization
        payload_hash = hashlib.sha256(payload).hexdigest()
        asset_root = build_asset_root(context, config_key=config_key)
        manifest_key = f"{asset_root}/_manifest.json"
        layer = asset_root.split("/")[0]
        code_location = asset_root.split("/")[1] if "/" in asset_root else "unknown"
        asset_name = context.asset_key.to_user_string()

        manifest = load_or_create_manifest(
            self.client.get_object, manifest_key, asset_name, code_location, layer
        )
        if (
            manifest.materializations
            and manifest.materializations[-1].content_hash == payload_hash
        ):
            context.log.info("Content unchanged (hash=%s) — skipping write", payload_hash[:12])
            context.add_output_metadata({"skipped": True, "reason": "content_unchanged"})
            return

        # Write data (overwrite in place — MinIO versioning preserves history)
        data_key = f"{prefix}/data{ext}"
        self.client.put_object(data_key, payload)

        # Write enhanced metadata sidecar
        count = ser_meta.get("count", 0)
        metadata = self._build_metadata(
            context, ser_meta["format"], count, len(payload), type_hint, obj,
            config_key=config_key,
        )
        self.client.put_object(
            f"{prefix}/_metadata.json",
            json.dumps(metadata, indent=2).encode("utf-8"),
        )

        # Update manifest at asset root (config-aware) — reuse already-loaded manifest
        partition_str = str(context.asset_partition_key) if context.has_asset_partitions else None
        record = make_record(
            run_id=context.run_id,
            fmt=ser_meta["format"],
            count=count,
            size_bytes=len(payload),
            partition=partition_str,
            config_key=config_key,
            content_hash=payload_hash,
        )
        manifest.add_materialization(record)
        self.client.put_object(manifest_key, manifest.to_bytes())

        output_meta = {
            "s3_path": f"s3://{self.bucket}/{data_key}",
            "format": metadata["format"],
            "size_bytes": len(payload),
            "row_count": count,
            "layer": metadata["layer"],
        }
        if config_key:
            output_meta["config_key"] = config_key
        context.add_output_metadata(output_meta)

    def _get_input_type_hint(self, context: InputContext) -> type | None:
        try:
            th = context.dagster_type.typing_type
            if th is typing.Any:
                return None
            return th
        except Exception:
            return None

    def _load_single(
        self, context: InputContext, partition_key=None
    ) -> typing.Any:
        prefix = build_input_prefix(context, partition_key=partition_key)
        meta_bytes = self.client.get_object(f"{prefix}/_metadata.json")
        metadata = json.loads(meta_bytes)
        ext = "." + metadata["format"]
        payload = self.client.get_object(f"{prefix}/data{ext}")
        type_hint = self._get_input_type_hint(context)
        return deserialize(payload, ext, metadata, type_hint=type_hint)

    def load_input(self, context: InputContext) -> typing.Any:
        if context.has_asset_partitions:
            keys = context.asset_partition_keys
            if len(keys) > 1:
                # Multi-partition fan-in: return dict keyed by partition
                return {k: self._load_single(context, k) for k in keys}
            return self._load_single(context, keys[0])
        return self._load_single(context)
