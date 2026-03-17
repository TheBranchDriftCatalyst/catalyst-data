"""MCP client abstractions for contract validation."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class MCPClient(ABC):
    """Abstract base class for MCP tool invocation."""

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool and return the result."""
        ...


class StdioMCPClient(MCPClient):
    """MCP client that communicates with a server subprocess via stdio."""

    def __init__(self, command: list[str], *, timeout: float = 30.0) -> None:
        self._command = command
        self._timeout = timeout
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stop(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("StdioMCPClient not started; call start() first")

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        payload = json.dumps(request) + "\n"
        self._process.stdin.write(payload.encode())
        await self._process.stdin.drain()

        try:
            line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            await self.stop()
            raise TimeoutError(
                f"MCP server did not respond within {self._timeout}s"
            )
        response = json.loads(line.decode())

        if "error" in response:
            raise RuntimeError(f"MCP tool error: {response['error']}")

        result = response.get("result", {})
        # MCP tool results come in content array; extract text content
        if "content" in result and isinstance(result["content"], list):
            for item in result["content"]:
                if item.get("type") == "text":
                    return json.loads(item["text"])
        return result


class DirectMCPClient(MCPClient):
    """MCP client that imports and calls Python functions directly."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        method = getattr(self._handler, name, None)
        if method is None:
            raise AttributeError(f"Handler has no tool method: {name}")

        result = method(**arguments)
        if asyncio.iscoroutine(result):
            result = await result
        return result


class MockMCPClient(MCPClient):
    """MCP client that returns configurable responses for testing."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self._responses: dict[str, Any] = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def set_response(self, tool_name: str, response: Any) -> None:
        self._responses[tool_name] = response

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name in self._responses:
            resp = self._responses[name]
            if callable(resp):
                return resp(arguments)
            return resp
        return {"verdict": "valid", "errors": []}
