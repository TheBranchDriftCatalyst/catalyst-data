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
