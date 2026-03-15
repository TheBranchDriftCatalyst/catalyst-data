"""Build S3 keys with medallion layers and Hive-style partition paths."""

from __future__ import annotations

import os
import re

from dagster import InputContext, OutputContext


def _code_location_from_context(context: OutputContext | InputContext) -> str:
    """Extract code location name from run context, falling back to env var.

    For InputContext, checks upstream metadata for ``source_code_location``
    override — enables cross-code-location reads via SourceAssets.
    """
    # Cross-code-location override: SourceAsset metadata can specify the
    # code location that actually wrote the data.
    if isinstance(context, InputContext):
        meta = getattr(context.upstream_output, "definition_metadata", None) or {}
        override = meta.get("source_code_location")
        if override:
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
            # InputContext: read from upstream output's definition metadata
            meta = (
                getattr(context.upstream_output, "definition_metadata", None) or {}
            )
        return meta.get("layer", "raw")
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
