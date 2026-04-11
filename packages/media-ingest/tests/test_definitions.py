"""Test media-ingest Dagster definitions load correctly."""

from media_ingest import defs


def test_definitions_load():
    """Verify all 11 assets are registered."""
    assets = list(defs.resolve_asset_graph().get_all_asset_keys())
    assert len(assets) == 11


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


def test_sensor_asset_selections_are_closed():
    """Every sensor with an asset_selection must form a closed subgraph.

    For each selected asset, every partitioned parent MUST also be in the
    selection. Otherwise a sensor-triggered run will fail at IO manager load
    time with NoSuchKey, because the parent partition will never have been
    materialized inside the run.

    This is the guardrail for the April 5 regression where media_diarization
    was split out of media_transcriptions but not added to
    media_document_sensor's asset_selection — every run failed trying to
    load media_diarization as an input to media_chunks.
    """
    graph = defs.resolve_asset_graph()
    for sensor in defs.sensors:
        if sensor.asset_selection is None:
            continue
        selection = sensor.asset_selection.resolve(graph)
        for key in selection:
            node = graph.get(key)
            for parent in node.parent_keys:
                parent_node = graph.get(parent)
                if parent_node.partitions_def is None:
                    # Unpartitioned parents are expected to be materialized
                    # separately (e.g. by discovery jobs) — fine to exclude.
                    continue
                assert parent in selection, (
                    f"Sensor '{sensor.name}' selects {key} but its partitioned "
                    f"parent {parent} is NOT in the selection. Runs will fail "
                    f"loading {parent} from the IO manager. Add "
                    f"AssetKey({list(parent.path)!r}) to asset_selection."
                )
