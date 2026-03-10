"""Multi-format serialization: JSONL, JSON, Pickle."""

from __future__ import annotations

import json
import pickle
import typing
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel


def _is_pydantic_model(tp: type | None) -> bool:
    try:
        return isinstance(tp, type) and issubclass(tp, BaseModel)
    except TypeError:
        return False


def _is_list_of_pydantic(tp: type | None) -> bool:
    origin = typing.get_origin(tp)
    if origin is list:
        args = typing.get_args(tp)
        if args and _is_pydantic_model(args[0]):
            return True
    return False


def _detect_format(obj: Any, type_hint: type | None) -> str:
    """Determine serialization format from type hint or runtime inspection."""
    # Type-hint based detection
    if type_hint and type_hint is not typing.Any:
        if _is_list_of_pydantic(type_hint):
            return "jsonl"
        if _is_pydantic_model(type_hint):
            return "json"
        if type_hint is dict:
            return "json"
        origin = typing.get_origin(type_hint)
        if origin is list:
            args = typing.get_args(type_hint)
            if args and args[0] is dict:
                return "jsonl"

    # Runtime inspection fallback
    if isinstance(obj, list) and obj and isinstance(obj[0], BaseModel):
        return "jsonl"
    if isinstance(obj, BaseModel):
        return "json"
    if isinstance(obj, dict):
        return "json"
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return "jsonl"

    return "pkl"


def serialize(data: Any, type_hint: type | None) -> tuple[bytes, str, dict]:
    """Serialize data to bytes.

    Returns:
        (payload_bytes, extension_with_dot, metadata_dict)
    """
    fmt = _detect_format(data, type_hint)
    metadata = {
        "format": fmt,
        "type": str(type_hint) if type_hint else "unknown",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if fmt == "jsonl":
        lines = []
        for item in data:
            if isinstance(item, BaseModel):
                lines.append(item.model_dump_json())
            else:
                lines.append(json.dumps(item, default=str))
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        metadata["count"] = len(data)
        return payload, ".jsonl", metadata

    if fmt == "json":
        if isinstance(data, BaseModel):
            payload = data.model_dump_json().encode("utf-8")
        else:
            payload = json.dumps(data, default=str).encode("utf-8")
        metadata["count"] = 1
        return payload, ".json", metadata

    # Pickle fallback
    payload = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
    metadata["count"] = len(data) if hasattr(data, "__len__") else 1
    return payload, ".pkl", metadata


def _extract_schema(obj: Any) -> dict:
    """Extract field names from data for metadata sidecar."""
    if isinstance(obj, list) and obj:
        first = obj[0]
        if isinstance(first, BaseModel):
            return {"fields": list(first.model_fields.keys())}
        if isinstance(first, dict):
            return {"fields": list(first.keys())}
    if isinstance(obj, BaseModel):
        return {"fields": list(obj.model_fields.keys())}
    if isinstance(obj, dict):
        return {"fields": list(obj.keys())}
    return {}


def deserialize(payload: bytes, extension: str, metadata: dict) -> Any:
    """Deserialize bytes back to Python objects.

    JSONL -> list[dict], JSON -> dict, Pickle -> original type.
    """
    fmt = metadata.get("format", extension.lstrip("."))

    if fmt == "jsonl":
        lines = payload.decode("utf-8").strip().split("\n")
        return [json.loads(line) for line in lines if line]

    if fmt == "json":
        return json.loads(payload.decode("utf-8"))

    # Pickle
    return pickle.loads(payload)  # noqa: S301
