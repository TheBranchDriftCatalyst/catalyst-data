"""Test media-ingest Dagster definitions load correctly."""

from media_ingest import defs


def test_definitions_load():
    """Verify all 8 assets are registered."""
    assets = list(defs.resolve_asset_graph().get_all_asset_keys())
    assert len(assets) == 8


def test_definitions_has_io_manager():
    """Verify MinioIOManager is configured."""
    resources = defs.resources
    assert "io_manager" in resources


def test_definitions_has_llm_resource():
    """Verify LLMResource is configured (needed for mentions/assertions)."""
    resources = defs.resources
    assert "llm" in resources


def test_definitions_has_sensor():
    """Verify media_document_sensor is registered."""
    sensors = defs.sensors
    sensor_names = [s.name for s in sensors]
    assert "media_document_sensor" in sensor_names


def test_partitioned_assets_share_partition_def():
    """Verify partitioned assets all use the same DynamicPartitionsDefinition."""
    from dagster import AssetKey

    from media_ingest.partitions import media_partitions

    partitioned_keys = {
        AssetKey("media_transcriptions"),
        AssetKey("media_chunks"),
        AssetKey("media_mentions"),
        AssetKey("media_assertions"),
        AssetKey("media_embeddings"),
    }
    specs = defs.resolve_all_asset_specs()
    matched = 0
    for spec in specs:
        if spec.key in partitioned_keys:
            assert spec.partitions_def is media_partitions, (
                f"{spec.key} does not use media_partitions"
            )
            matched += 1
    assert matched == len(partitioned_keys), (
        f"Expected {len(partitioned_keys)} partitioned assets, found {matched}"
    )


def test_unpartitioned_assets():
    """Verify discovery assets remain unpartitioned."""
    from dagster import AssetKey

    unpartitioned_keys = {
        AssetKey("media_files"),
        AssetKey("media_metadata"),
        AssetKey("media_documents"),
    }
    specs = defs.resolve_all_asset_specs()
    for spec in specs:
        if spec.key in unpartitioned_keys:
            assert spec.partitions_def is None, (
                f"{spec.key} should not be partitioned"
            )
