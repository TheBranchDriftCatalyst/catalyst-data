"""Test congress-data Dagster definitions load correctly."""

from congress_data import defs


def test_definitions_load():
    """Verify all 23 assets are registered (4 head + 11 bill tail + 8 member tail)."""
    assets = list(defs.resolve_asset_graph().get_all_asset_keys())
    assert len(assets) == 23


def test_definitions_has_io_manager():
    """Verify MinioIOManager is configured."""
    resources = defs.resources
    assert "io_manager" in resources
