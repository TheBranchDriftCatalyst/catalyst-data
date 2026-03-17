"""Tests for MCP client implementations."""

import pytest

from catalyst_langgraph.clients.mcp import DirectMCPClient, MockMCPClient


@pytest.mark.asyncio
async def test_mock_mcp_default_response():
    client = MockMCPClient()
    result = await client.call_tool("validate_mentions", {"mentions": []})
    assert result["verdict"] == "valid"
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_mock_mcp_custom_response():
    client = MockMCPClient()
    client.set_response(
        "validate_mentions",
        {"verdict": "rejected", "errors": ["Missing entity_type"]},
    )
    result = await client.call_tool("validate_mentions", {"mentions": [{"name": "x"}]})
    assert result["verdict"] == "rejected"
    assert len(result["errors"]) == 1


@pytest.mark.asyncio
async def test_mock_mcp_tracks_calls():
    client = MockMCPClient()
    await client.call_tool("validate_mentions", {"mentions": [{"a": 1}]})
    await client.call_tool("validate_propositions", {"propositions": []})
    assert len(client.calls) == 2
    assert client.calls[0] == ("validate_mentions", {"mentions": [{"a": 1}]})
    assert client.calls[1] == ("validate_propositions", {"propositions": []})


@pytest.mark.asyncio
async def test_mock_mcp_callable_response():
    client = MockMCPClient()
    client.set_response(
        "validate_mentions",
        lambda args: {
            "verdict": "accepted" if len(args.get("mentions", [])) > 0 else "rejected",
            "errors": [],
        },
    )
    result_empty = await client.call_tool("validate_mentions", {"mentions": []})
    assert result_empty["verdict"] == "rejected"

    result_full = await client.call_tool(
        "validate_mentions", {"mentions": [{"name": "x"}]}
    )
    assert result_full["verdict"] == "accepted"


# --- DirectMCPClient tests ---


class SyncHandler:
    """Handler with a synchronous tool method."""

    def validate(self, **kwargs):
        return {"verdict": "accepted", "data": kwargs}


class AsyncHandler:
    """Handler with an async tool method."""

    async def validate(self, **kwargs):
        return {"verdict": "accepted", "data": kwargs}


@pytest.mark.asyncio
async def test_direct_mcp_sync_handler():
    handler = SyncHandler()
    client = DirectMCPClient(handler)
    result = await client.call_tool("validate", {"key": "value"})
    assert result["verdict"] == "accepted"
    assert result["data"] == {"key": "value"}


@pytest.mark.asyncio
async def test_direct_mcp_async_handler():
    handler = AsyncHandler()
    client = DirectMCPClient(handler)
    result = await client.call_tool("validate", {"key": "value"})
    assert result["verdict"] == "accepted"
    assert result["data"] == {"key": "value"}


@pytest.mark.asyncio
async def test_direct_mcp_missing_method():
    handler = SyncHandler()
    client = DirectMCPClient(handler)
    with pytest.raises(AttributeError, match="Handler has no tool method: nonexistent"):
        await client.call_tool("nonexistent", {})
