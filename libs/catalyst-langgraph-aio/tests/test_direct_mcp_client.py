"""Unit tests for DirectMCPClient."""

from __future__ import annotations

import asyncio

import pytest

from catalyst_langgraph.clients.mcp import DirectMCPClient


class SyncHandler:
    """Fake handler with sync methods."""

    def validate_mentions(self, **kwargs):
        return {"verdict": "accepted", "args": kwargs}


class AsyncHandler:
    """Fake handler with async methods."""

    async def validate_mentions(self, **kwargs):
        return {"verdict": "accepted", "args": kwargs}


class TestDirectMCPClient:
    @pytest.mark.asyncio
    async def test_calls_sync_handler(self):
        client = DirectMCPClient(SyncHandler())
        result = await client.call_tool("validate_mentions", {"text": "hello"})
        assert result["verdict"] == "accepted"
        assert result["args"] == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_calls_async_handler(self):
        client = DirectMCPClient(AsyncHandler())
        result = await client.call_tool("validate_mentions", {"text": "hello"})
        assert result["verdict"] == "accepted"
        assert result["args"] == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_missing_method_raises(self):
        client = DirectMCPClient(SyncHandler())
        with pytest.raises(AttributeError, match="no tool method"):
            await client.call_tool("nonexistent_tool", {})

    @pytest.mark.asyncio
    async def test_passes_kwargs_correctly(self):
        client = DirectMCPClient(SyncHandler())
        result = await client.call_tool(
            "validate_mentions", {"a": 1, "b": "two", "c": [3]}
        )
        assert result["args"] == {"a": 1, "b": "two", "c": [3]}
