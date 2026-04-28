"""Filesystem IO manager that mirrors MinioIOManager's JSON/JSONL format.

Writes human-readable files to a local directory so you can inspect
intermediate pipeline outputs. Drop-in replacement for MinioIOManager
in local/test contexts.

Directory structure matches the S3 medallion layout:
  {base_dir}/{layer}/{code_location}/{group}/{asset_name}/[{partition}/]data.{json,jsonl}
"""

from __future__ import annotations

import json
import pickle
import typing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from dagster import ConfigurableIOManager, InputContext, OutputContext
from pydantic import BaseModel

from dagster_io.logging import get_logger
from dagster_io.path_builder import build_input_prefix, build_output_prefix

logger = get_logger(__name__)


def _to_serializable(obj: Any) -> Any:
    """Convert an object to a JSON-safe representation."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(v) for v in obj]
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}>"
    return obj


class LocalJsonIOManager(ConfigurableIOManager):
    """Filesystem IO manager using JSON/JSONL — same medallion paths as MinioIOManager.

    Adds an optional ``model_tag`` dimension for benchmark comparisons. When set,
    gold/platinum layer outputs get a ``model={tag}`` segment in the path so
    extraction results from different LLM models don't overwrite each other.

    Path layout:
        {base_dir}/{layer}/{code_location}/{group}/{asset}/[model={tag}/][{partition}/]data.jsonl

    Usage:
        # Without model tagging (same as MinioIOManager):
        resources={"io_manager": LocalJsonIOManager(base_dir=".test-output/media-ingest")}

        # With model tagging for benchmark runs:
        resources={"io_manager": LocalJsonIOManager(
            base_dir=".test-output/media-ingest",
            model_tag="mistral:latest",
        )}
    """

    base_dir: str = "/tmp/dagster-local"
    model_tag: str = ""  # set to LLM_MODEL value to key gold-layer outputs by model

    # Layers where model_tag is injected into the path
    _MODEL_KEYED_LAYERS: ClassVar[set[str]] = {"gold", "platinum"}

    def _prefix(self, context: OutputContext | InputContext) -> str:
        prefix = build_output_prefix(context) if isinstance(context, OutputContext) else build_input_prefix(context)

        # Inject model tag for gold/platinum layers
        if self.model_tag:
            layer = prefix.split("/")[0] if "/" in prefix else ""
            if layer in self._MODEL_KEYED_LAYERS:
                # Insert model={tag} after the asset name, before partition
                # e.g. gold/media_ingest/media/media_mentions/model=mistral/data.jsonl
                safe_tag = self.model_tag.replace("/", "_").replace(":", "_")
                prefix = self._inject_model_segment(prefix, safe_tag)
        return prefix

    @staticmethod
    def _inject_model_segment(prefix: str, tag: str) -> str:
        """Insert model={tag} into the path after the asset name segment.

        Input:  gold/media_ingest/media/media_mentions/119-hres-1
        Output: gold/media_ingest/media/media_mentions/model=mistral_latest/119-hres-1
        """
        parts = prefix.split("/")
        # Layout: layer/code_location/group/asset/[partition...]
        # Insert after index 3 (the asset name)
        if len(parts) >= 4:
            return "/".join(parts[:4] + [f"model={tag}"] + parts[4:])
        return f"{prefix}/model={tag}"

    def _detect_format(self, obj: Any, type_hint: type | None) -> str:
        if isinstance(obj, list):
            if obj and isinstance(obj[0], (BaseModel, dict)):
                return "jsonl"
            return "jsonl"
        if isinstance(obj, (BaseModel, dict)):
            return "json"
        return "pkl"

    def handle_output(self, context: OutputContext, obj: Any) -> None:
        prefix = self._prefix(context)
        out_dir = Path(self.base_dir) / prefix
        out_dir.mkdir(parents=True, exist_ok=True)

        type_hint = context.dagster_type.typing_type if context.dagster_type else None
        fmt = self._detect_format(obj, type_hint)

        if fmt == "jsonl":
            path = out_dir / "data.jsonl"
            items = obj if isinstance(obj, list) else [obj]
            with open(path, "w") as f:
                for item in items:
                    f.write(json.dumps(_to_serializable(item), default=str) + "\n")
            count = len(items)

        elif fmt == "json":
            path = out_dir / "data.json"
            with open(path, "w") as f:
                json.dump(_to_serializable(obj), f, indent=2, default=str)
            count = 1

        else:
            path = out_dir / "data.pkl"
            with open(path, "wb") as f:
                pickle.dump(obj, f)
            count = 1

        # Write metadata sidecar (same as MinioIOManager)
        meta = {
            "format": fmt,
            "type": str(type_hint) if type_hint else "unknown",
            "count": count,
            "timestamp": datetime.now(UTC).isoformat(),
            "asset_key": context.asset_key.to_user_string() if context.asset_key else "",
            "partition": context.partition_key if context.has_partition_key else None,
            "size_bytes": path.stat().st_size,
        }
        with open(out_dir / "_metadata.json", "w") as f:
            json.dump(meta, f, indent=2)

        logger.info(
            "LocalJsonIOManager: wrote %s (%d items, %d bytes) to %s",
            fmt,
            count,
            path.stat().st_size,
            path,
        )

    def load_input(self, context: InputContext) -> Any:
        prefix = self._prefix(context)
        in_dir = Path(self.base_dir) / prefix

        # Try formats in order: jsonl → json → pkl
        jsonl_path = in_dir / "data.jsonl"
        if jsonl_path.exists():
            with open(jsonl_path) as f:
                rows = [json.loads(line) for line in f if line.strip()]
            logger.info("LocalJsonIOManager: loaded %d rows from %s", len(rows), jsonl_path)

            # Reconstruct Pydantic models if type hint is available
            type_hint = context.dagster_type.typing_type if context.dagster_type else None
            if type_hint:
                origin = typing.get_origin(type_hint)
                if origin is list:
                    args = typing.get_args(type_hint)
                    if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                        return [args[0].model_validate(row) for row in rows]
            return rows

        json_path = in_dir / "data.json"
        if json_path.exists():
            with open(json_path) as f:
                data = json.load(f)
            logger.info("LocalJsonIOManager: loaded JSON from %s", json_path)

            type_hint = context.dagster_type.typing_type if context.dagster_type else None
            if type_hint and isinstance(type_hint, type) and issubclass(type_hint, BaseModel):
                return type_hint.model_validate(data)
            return data

        pkl_path = in_dir / "data.pkl"
        if pkl_path.exists():
            with open(pkl_path, "rb") as f:
                return pickle.load(f)  # noqa: S301

        raise FileNotFoundError(f"No data found at {in_dir} (tried .jsonl, .json, .pkl)")
