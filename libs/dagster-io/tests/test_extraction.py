"""Tests for dagster_io.extraction — thin wrapper around ExtractionResource.

Wave 1 / Step 3 (bead llm-g0b): the legacy ``_build_pipelines``-based
tests were retired alongside the LangGraph driver they exercised.
``extract_validated`` is now a one-screen wrapper that constructs an
``ExtractionResource`` and calls ``extract_assertions``. The bulk of
extraction-shape coverage lives in catalyst-exgraph (the resource
owns the pipeline + Mention/Assertion construction).

What remains here: a thin smoke test that the wrapper short-circuits
on empty input and that the code_location → label_pack mapping does
the right thing. Anything that requires running the full ensemble +
AMR pipeline is mocked via patching ``ExtractionResource`` itself.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_extract_validated_empty_chunks():
    """Empty chunks short-circuits to an empty ExtractionResult — no resource construction."""
    from dagster_io.extraction import extract_validated

    result = extract_validated([], "test")
    assert result.mentions == []
    assert result.assertions == []


def test_extract_validated_routes_through_resource():
    """Non-empty chunks instantiate ExtractionResource and call extract_assertions.

    Stubs out the lazy ``catalyst_exgraph.resource`` import via ``sys.modules``
    so the test doesn't pull in the (currently broken) catalyst-llm resource
    module at all — the wrapper's job is just to delegate, and that's what
    we're asserting.
    """
    import sys
    import types

    from dagster_io.extraction import extract_validated

    mock_result = MagicMock()
    mock_result.mentions = []
    mock_result.assertions = []

    instance = MagicMock()
    instance.extract_assertions.return_value = mock_result
    mock_cls = MagicMock(return_value=instance)

    stub_module = types.ModuleType("catalyst_exgraph.resource")
    stub_module.ExtractionResource = mock_cls

    with patch.dict(sys.modules, {"catalyst_exgraph.resource": stub_module}):
        # Sentinel "chunk" — the wrapper just forwards it; no shape checks here.
        chunks = [MagicMock()]
        result = extract_validated(chunks, "media_ingest", max_concurrency=3)

    assert result is mock_result
    assert mock_cls.call_count == 1
    kwargs = mock_cls.call_args.kwargs
    assert kwargs["label_pack_id"] == "media"
    assert kwargs["max_concurrency"] == 3
    instance.extract_assertions.assert_called_once()
    call_kwargs = instance.extract_assertions.call_args.kwargs
    assert call_kwargs["chunks"] is chunks
    assert call_kwargs["code_location"] == "media_ingest"


def test_extract_validated_code_location_to_label_pack_mapping():
    """code_location maps to the right label_pack_id."""
    from dagster_io.extraction import _resolve_label_pack

    assert _resolve_label_pack("congress") == "congress"
    assert _resolve_label_pack("congress_data") == "congress"
    assert _resolve_label_pack("media") == "media"
    assert _resolve_label_pack("media_ingest") == "media"
    assert _resolve_label_pack("open_leaks") == "generic"
    assert _resolve_label_pack("") == "generic"
    assert _resolve_label_pack("unknown") == "generic"
