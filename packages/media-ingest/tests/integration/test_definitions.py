"""Tests for Dagster definitions — asset registration, resources, sensors."""

from dagster import AssetKey
from media_ingest import defs
from media_ingest.partitions import media_partitions


class TestAssetRegistration:
    def test_all_assets_registered(self):
        assets = list(defs.resolve_asset_graph().get_all_asset_keys())
        assert len(assets) >= 14

    def test_io_managers_configured(self):
        resources = defs.resources
        assert "io_manager" in resources
        assert "optional_io_manager" in resources

    def test_llm_resource_configured(self):
        assert "llm" in defs.resources

    def test_sensor_registered(self):
        sensor_names = [s.name for s in defs.sensors]
        assert "media_document_sensor" in sensor_names


class TestPartitions:
    def test_partitioned_assets_share_definition(self):
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
                assert spec.partitions_def is media_partitions, f"{spec.key} uses wrong partitions"
                matched += 1
        assert matched == len(partitioned_keys)

    def test_unpartitioned_assets(self):
        unpartitioned_keys = {
            AssetKey("media_files"),
            AssetKey("media_metadata"),
            AssetKey("media_documents"),
        }
        specs = defs.resolve_all_asset_specs()
        for spec in specs:
            if spec.key in unpartitioned_keys:
                assert spec.partitions_def is None, f"{spec.key} should not be partitioned"


class TestSensorClosedGraph:
    def test_sensor_selections_are_closed(self):
        """Every sensor's selected partitioned parents must also be selected.

        Prevents regressions where adding an asset to the DAG but not the
        sensor causes runs to fail loading upstream partitions.
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
                        continue
                    assert parent in selection, (
                        f"Sensor '{sensor.name}' selects {key} but its partitioned "
                        f"parent {parent} is NOT in the selection."
                    )
