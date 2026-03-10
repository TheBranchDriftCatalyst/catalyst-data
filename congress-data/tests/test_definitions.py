"""Test congress-data Dagster definitions load correctly."""

from congress_data import defs


def test_definitions_load():
    """Verify all 8 assets are registered."""
    assets = list(defs.resolve_asset_graph().get_all_asset_keys())
    assert len(assets) == 8


def test_definitions_has_io_manager():
    """Verify MinioIOManager is configured."""
    resources = defs.resources
    assert "io_manager" in resources
