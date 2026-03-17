"""Tests for StdioMCPClient timeout behavior."""

from __future__ import annotations

import asyncio

import pytest

from catalyst_langgraph.clients.mcp import StdioMCPClient


@pytest.mark.asyncio
async def test_stdio_mcp_client_timeout():
    """StdioMCPClient raises TimeoutError when server doesn't respond in time."""
    # Use 'sleep' as a subprocess that never writes to stdout
    client = StdioMCPClient(["sleep", "60"], timeout=0.1)
    await client.start()

    try:
        with pytest.raises(TimeoutError, match="did not respond within 0.1s"):
            await client.call_tool("test_tool", {"arg": "value"})
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_stdio_mcp_client_default_timeout():
    """StdioMCPClient has 30s default timeout."""
    client = StdioMCPClient(["echo", "hello"])
    assert client._timeout == 30.0


@pytest.mark.asyncio
async def test_stdio_mcp_client_custom_timeout():
    """StdioMCPClient accepts custom timeout."""
    client = StdioMCPClient(["echo", "hello"], timeout=60.0)
    assert client._timeout == 60.0


@pytest.mark.asyncio
async def test_stdio_mcp_client_stops_process_on_timeout():
    """After timeout, the subprocess should be terminated."""
    client = StdioMCPClient(["sleep", "60"], timeout=0.1)
    await client.start()

    assert client._process is not None
    assert client._process.returncode is None  # still running

    try:
        with pytest.raises(TimeoutError):
            await client.call_tool("test_tool", {"arg": "value"})
    finally:
        # Process should have been terminated by the timeout handler
        assert client._process.returncode is not None
