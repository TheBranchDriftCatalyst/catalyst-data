"""Behavioral tests for the strangler fig dispatcher.

Tests verify that EXGRAPH_ENABLED env var toggles between v1 (catalyst-langgraph)
and v2 (catalyst-exgraph) extraction graphs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from catalyst_exgraph.dispatch import _LegacyAdapter, build_extraction_graph

# =====================================================================
# 1. EXGRAPH_ENABLED=false -> returns v1 graph
# =====================================================================


@patch("catalyst_exgraph.dispatch.EXGRAPH_ENABLED", False)
@patch("catalyst_exgraph.dispatch._build_v1")
def test_dispatch_disabled_returns_v1_graph(mock_v1):
    """When EXGRAPH_ENABLED is false, dispatcher delegates to v1 builder."""
    sentinel_graph = MagicMock(name="v1_graph")
    mock_v1.return_value = sentinel_graph

    llm_client = MagicMock()
    mcp_client = MagicMock()
    repository = MagicMock()

    graph, client = build_extraction_graph(llm_client, mcp_client, repository)

    mock_v1.assert_called_once_with(llm_client, mcp_client, repository)
    assert graph is sentinel_graph
    assert client is llm_client


# =====================================================================
# 2. EXGRAPH_ENABLED=true -> returns v2 adapter
# =====================================================================


@patch("catalyst_exgraph.dispatch.EXGRAPH_ENABLED", True)
@patch("catalyst_exgraph.dispatch._build_v2")
def test_dispatch_enabled_returns_v2_adapter(mock_v2):
    """When EXGRAPH_ENABLED is true, dispatcher returns a LegacyAdapter wrapping v2 pipeline."""
    sentinel_adapter = MagicMock(name="v2_adapter")
    mock_v2.return_value = sentinel_adapter

    llm_client = MagicMock()
    mcp_client = MagicMock()
    repository = MagicMock()

    graph, client = build_extraction_graph(llm_client, mcp_client, repository)

    mock_v2.assert_called_once_with(llm_client, mcp_client)
    assert graph is sentinel_adapter
    assert client is llm_client


# =====================================================================
# 3. v1 path does not call v2 builder and vice versa
# =====================================================================


@patch("catalyst_exgraph.dispatch.EXGRAPH_ENABLED", False)
@patch("catalyst_exgraph.dispatch._build_v2")
@patch("catalyst_exgraph.dispatch._build_v1")
def test_v1_path_does_not_call_v2(mock_v1, mock_v2):
    """When disabled, _build_v2 is never called."""
    mock_v1.return_value = MagicMock()
    build_extraction_graph(MagicMock(), MagicMock(), MagicMock())

    mock_v2.assert_not_called()


@patch("catalyst_exgraph.dispatch.EXGRAPH_ENABLED", True)
@patch("catalyst_exgraph.dispatch._build_v1")
@patch("catalyst_exgraph.dispatch._build_v2")
def test_v2_path_does_not_call_v1(mock_v2, mock_v1):
    """When enabled, _build_v1 is never called."""
    mock_v2.return_value = MagicMock()
    build_extraction_graph(MagicMock(), MagicMock(), MagicMock())

    mock_v1.assert_not_called()


# =====================================================================
# 4. LegacyAdapter.ainvoke produces legacy-shaped output
# =====================================================================


async def test_legacy_adapter_maps_exgraph_state_to_flat_keys():
    """LegacyAdapter.ainvoke() calls pipeline, then maps nested state to flat keys."""
    mock_pipeline = MagicMock()
    mock_pipeline.ainvoke = MagicMock()

    # Simulate pipeline returning ExGraphState
    exgraph_result = {
        "stages": {
            "ner": {
                "accepted": [{"text": "Alice", "mention_type": "PERSON"}],
                "retry_count": 1,
                "status": "completed",
            },
            "spo": {
                "accepted": [{"subject": "Alice", "predicate": "met", "object": "Bob"}],
                "retry_count": 0,
                "status": "completed",
            },
        },
        "audit_events": [{"node_name": "extract_ner"}],
        "status": "completed",
    }

    async def fake_ainvoke(state):
        return exgraph_result

    mock_pipeline.ainvoke = fake_ainvoke
    adapter = _LegacyAdapter(mock_pipeline)

    result = await adapter.ainvoke({"raw_text": "test"})

    assert result["accepted_mentions"] == [{"text": "Alice", "mention_type": "PERSON"}]
    assert result["accepted_propositions"] == [{"subject": "Alice", "predicate": "met", "object": "Bob"}]
    assert result["mention_retry_count"] == 1
    assert result["proposition_retry_count"] == 0
    assert result["status"] == "completed"
    assert len(result["audit_events"]) == 1


# =====================================================================
# 5. LegacyAdapter with empty pipeline result
# =====================================================================


async def test_legacy_adapter_empty_pipeline_result():
    """LegacyAdapter with empty stages returns empty lists."""

    async def fake_ainvoke(state):
        return {"stages": {}, "audit_events": [], "status": "completed"}

    mock_pipeline = MagicMock()
    mock_pipeline.ainvoke = fake_ainvoke
    adapter = _LegacyAdapter(mock_pipeline)

    result = await adapter.ainvoke({})

    assert result["accepted_mentions"] == []
    assert result["accepted_propositions"] == []
    assert result["status"] == "completed"


# =====================================================================
# 6. Return shape is always a 2-tuple (graph, client)
# =====================================================================


@patch("catalyst_exgraph.dispatch.EXGRAPH_ENABLED", False)
@patch("catalyst_exgraph.dispatch._build_v1", return_value=MagicMock())
def test_return_shape_is_2_tuple_v1(mock_v1):
    result = build_extraction_graph(MagicMock(), MagicMock(), MagicMock())
    assert isinstance(result, tuple)
    assert len(result) == 2


@patch("catalyst_exgraph.dispatch.EXGRAPH_ENABLED", True)
@patch("catalyst_exgraph.dispatch._build_v2", return_value=MagicMock())
def test_return_shape_is_2_tuple_v2(mock_v2):
    result = build_extraction_graph(MagicMock(), MagicMock(), MagicMock())
    assert isinstance(result, tuple)
    assert len(result) == 2
