"""Test congress-data Dagster definitions load correctly."""

from congress_data import defs


def test_definitions_load():
    """Verify all 12 assets are registered (9 original + 3 EDC gold layer)."""
    assets = list(defs.resolve_asset_graph().get_all_asset_keys())
    assert len(assets) == 12


def test_definitions_has_io_manager():
    """Verify MinioIOManager is configured."""
    resources = defs.resources
    assert "io_manager" in resources
