"""Build S3 keys with medallion layers and Hive-style partition paths."""

from __future__ import annotations

import logging
import os
import re

from dagster import InputContext, OutputContext

logger = logging.getLogger(__name__)


def _unwrap_metadata_value(val):
    """Unwrap Dagster MetadataValue to its raw Python value.

    Dagster wraps metadata values in MetadataValue objects (e.g.,
    TextMetadataValue). This extracts the underlying .value/.text.
    """
    if val is None:
        return None
    # MetadataValue subclasses have a .value property
    if hasattr(val, "value"):
        return val.value
    # TextMetadataValue also has .text
    if hasattr(val, "text"):
        return val.text
    return val


def _get_metadata_str(meta: dict, key: str, default: str | None = None) -> str | None:
    """Get a string value from a metadata dict, unwrapping MetadataValue if needed."""
    val = meta.get(key, default)
    return _unwrap_metadata_value(val) if val is not default else default


def _get_upstream_metadata(context: InputContext) -> dict:
    """Get metadata dict from an InputContext's upstream output.

    Tries definition_metadata first, then metadata. Returns empty dict if
    neither is available.
    """
    upstream = context.upstream_output
    if upstream is None:
        return {}
    meta = getattr(upstream, "definition_metadata", None)
    if not meta:
        meta = getattr(upstream, "metadata", None)
    return meta or {}


def _code_location_from_context(context: OutputContext | InputContext) -> str:
    """Extract code location name from run context, falling back to env var.

    For InputContext, checks upstream metadata for ``source_code_location``
    override — enables cross-code-location reads via SourceAssets.
    """
    if isinstance(context, InputContext):
        meta = _get_upstream_metadata(context)
        override = _get_metadata_str(meta, "source_code_location")
        if override:
            logger.debug("path_builder: using source_code_location override=%s", override)
            return override
    try:
        origin = context.step_context.dagster_run.external_pipeline_origin
        return origin.external_repository_origin.code_location_origin.location_name
    except Exception:
        return os.environ.get("DAGSTER_CODE_LOCATION", "default")


def _group_from_asset_key(asset_key) -> str:
    """Derive group name from asset key.

    If multi-part key (e.g. congress/bills), use the first part.
    Otherwise derive from naming convention: congress_bills -> congress.
    """
    parts = asset_key.path
    if len(parts) > 1:
        return parts[0]
    return parts[0].split("_")[0]


def _extract_layer(context: OutputContext | InputContext) -> str:
    """Extract medallion layer from asset metadata, defaulting to 'raw'."""
    try:
        if isinstance(context, OutputContext):
            meta = context.definition_metadata or {}
        else:
            meta = _get_upstream_metadata(context)
        layer = _get_metadata_str(meta, "layer", "raw")
        return layer
    except Exception:
        return "raw"


# Date patterns: YYYY-MM-DD or YYYY-MM
_DATE_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_MONTH = re.compile(r"^\d{4}-\d{2}$")


def _hive_partition_segment(key: str) -> str:
    """Convert a partition key to Hive-style directory segments.

    '2026-03-09' -> 'year=2026/month=03/day=09'
    '2026-03'    -> 'year=2026/month=03'
    'other'      -> 'other'
    """
    if _DATE_FULL.match(key):
        parts = key.split("-")
        return f"year={parts[0]}/month={parts[1]}/day={parts[2]}"
    if _DATE_MONTH.match(key):
        parts = key.split("-")
        return f"year={parts[0]}/month={parts[1]}"
    return key


def hive_partition_path(context: OutputContext | InputContext, key=None) -> str:
    """Build partition path segment from context or explicit key.

    Handles single partition keys, MultiPartitionKeys, and explicit overrides.
    """
    if key is not None:
        return _hive_partition_segment(str(key))

    if not context.has_asset_partitions:
        return ""

    partition_key = context.asset_partition_key

    # Check for MultiPartitionKey
    try:
        from dagster import MultiPartitionKey

        if isinstance(partition_key, MultiPartitionKey):
            # Sort dimensions alphabetically for deterministic paths
            dims = sorted(partition_key.keys_by_dimension.items())
            return "/".join(
                f"{dim}={_hive_partition_segment(val)}" for dim, val in dims
            )
    except ImportError:
        pass

    return _hive_partition_segment(str(partition_key))


def build_asset_root(
    context: OutputContext | InputContext,
    config_key: str | None = None,
) -> str:
    """Build the root prefix for an asset.

    Without config_key: ``{layer}/{code_location}/{group}/{asset}``
    With config_key:    ``{layer}/{code_location}/{group}/{asset}/config={config_key}``
    """
    layer = _extract_layer(context)
    code_location = _code_location_from_context(context)
    group = _group_from_asset_key(context.asset_key)
    asset_name = context.asset_key.to_user_string().replace("/", "_")
    root = f"{layer}/{code_location}/{group}/{asset_name}"
    if config_key:
        root = f"{root}/config={config_key}"
    logger.debug("path_builder: build_asset_root=%s", root)
    return root


def build_output_prefix(
    context: OutputContext,
    config_key: str | None = None,
) -> str:
    """Build S3 key prefix for output: {layer}/{code_location}/{group}/{asset}[/config=...][/partition]"""
    root = build_asset_root(context, config_key=config_key)
    if context.has_asset_partitions:
        partition = hive_partition_path(context)
        return f"{root}/{partition}" if partition else root
    return root


def build_input_prefix(
    context: InputContext,
    partition_key=None,
    config_key: str | None = None,
) -> str:
    """Build S3 key prefix for input: {layer}/{code_location}/{group}/{asset}[/config=...][/partition]"""
    root = build_asset_root(context, config_key=config_key)
    if partition_key is not None:
        partition = hive_partition_path(context, key=partition_key)
        return f"{root}/{partition}" if partition else root
    if context.has_asset_partitions:
        partition = hive_partition_path(context)
        return f"{root}/{partition}" if partition else root
    return root
