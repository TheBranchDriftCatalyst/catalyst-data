"""Tests for the LLM-call retry decorator (CD-58ry).

Covers:
- Retryable HTTP status codes succeed after transient failures
- Non-retryable HTTP status codes (4xx besides 408/429) fail immediately
- Retryable httpx exception classes (ReadTimeout, ConnectError) trigger retries
- All-retries-exhausted re-raises the last exception
- WARNING log line fires on each retry (operator-visible degradation)
"""

from __future__ import annotations

import logging

import httpx
import pytest
from catalyst_langgraph.clients._retry import retry_llm_call


def _http_error(status: int) -> httpx.HTTPStatusError:
    """Build a synthetic HTTPStatusError with the given status code."""
    req = httpx.Request("POST", "http://localhost:11434/api/chat")
    resp = httpx.Response(status_code=status, request=req)
    return httpx.HTTPStatusError(message=f"HTTP {status}", request=req, response=resp)


@pytest.mark.asyncio
async def test_retryable_500_succeeds_after_two_failures():
    """Two transient 500s, then success — wrapped call returns the eventual result."""
    calls = {"n": 0}

    @retry_llm_call(name="test", attempts=3, base=0.001, cap=0.01)
    async def call() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(500)
        return "ok"

    assert await call() == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_non_retryable_400_fails_immediately():
    """4xx (besides 408/429) is a logic error — fail loud, no retry."""
    calls = {"n": 0}

    @retry_llm_call(name="test", attempts=3, base=0.001, cap=0.01)
    async def call() -> str:
        calls["n"] += 1
        raise _http_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        await call()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_429_is_retryable():
    """429 Too Many Requests is the canonical retry-with-backoff signal."""
    calls = {"n": 0}

    @retry_llm_call(name="test", attempts=3, base=0.001, cap=0.01)
    async def call() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise _http_error(429)
        return "ok"

    assert await call() == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_read_timeout_is_retryable():
    """Transport-layer ReadTimeout retries (Ollama daemon hiccup mid-stream)."""
    calls = {"n": 0}

    @retry_llm_call(name="test", attempts=3, base=0.001, cap=0.01)
    async def call() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ReadTimeout("read timed out")
        return "ok"

    assert await call() == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_exhausted_retries_reraises_last_exception():
    """After all attempts fail, the most recent exception propagates."""
    calls = {"n": 0}

    @retry_llm_call(name="test", attempts=3, base=0.001, cap=0.01)
    async def call() -> str:
        calls["n"] += 1
        raise _http_error(503)

    with pytest.raises(httpx.HTTPStatusError) as ei:
        await call()
    assert ei.value.response.status_code == 503
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_warning_logged_on_each_retry(caplog):
    """Each retry must surface a WARNING with name + attempt + delay."""
    caplog.set_level(logging.WARNING, logger="catalyst_langgraph.clients._retry")

    calls = {"n": 0}

    @retry_llm_call(name="myclient", attempts=3, base=0.001, cap=0.01)
    async def call() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(502)
        return "ok"

    await call()

    retry_records = [r for r in caplog.records if "retry" in r.getMessage()]
    assert len(retry_records) == 2  # two retries before success
    msg0 = retry_records[0].getMessage()
    assert "myclient" in msg0
    assert "retry 1/2" in msg0  # attempts-1 == max retries to display


@pytest.mark.asyncio
async def test_non_http_unrelated_exception_propagates():
    """An unrelated exception (e.g. ValueError) is not retryable — fail loud."""
    calls = {"n": 0}

    @retry_llm_call(name="test", attempts=3, base=0.001, cap=0.01)
    async def call() -> str:
        calls["n"] += 1
        raise ValueError("logic bug")

    with pytest.raises(ValueError):
        await call()
    assert calls["n"] == 1
