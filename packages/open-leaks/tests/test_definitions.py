"""Test open-leaks Dagster definitions load correctly."""

from open_leaks import defs


def test_definitions_load():
    """Verify all 13 assets are registered (10 original + 3 EDC gold layer)."""
    assets = list(defs.resolve_asset_graph().get_all_asset_keys())
    assert len(assets) == 13


def test_definitions_has_io_manager():
    """Verify MinioIOManager is configured."""
    resources = defs.resources
    assert "io_manager" in resources
