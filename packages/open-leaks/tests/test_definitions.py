"""Test open-leaks Dagster definitions load correctly."""

from open_leaks import defs


def test_definitions_load():
    """Verify all 10 assets are registered."""
    assets = list(defs.resolve_asset_graph().get_all_asset_keys())
    assert len(assets) == 10


def test_definitions_has_io_manager():
    """Verify MinioIOManager is configured."""
    resources = defs.resources
    assert "io_manager" in resources
