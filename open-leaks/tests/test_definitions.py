"""Test open-leaks Dagster definitions load correctly."""

from open_leaks import defs


def test_definitions_load():
    """Verify all 9 assets are registered."""
    assets = list(defs.get_asset_graph().all_asset_keys)
    assert len(assets) == 9


def test_definitions_has_io_manager():
    """Verify MinioIOManager is configured."""
    resources = defs.resources
    assert "io_manager" in resources
