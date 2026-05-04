"""Async exponential-backoff retry helper for Ollama-direct LLM clients.

Background (CD-58ry): nuextract.py + universalner.py call Ollama via raw
``httpx.AsyncClient.post`` with no retry coverage. A transient HTTP 500
(Ollama daemon hiccup, OOM, or context-length crash on a specific input)
kills the encoder for that doc and the surrounding ensemble has to
proceed without its vote. This module provides ``retry_llm_call`` —
a decorator that retries on the well-defined set of transient failure
modes with exponential backoff + full jitter.

Why a custom decorator instead of tenacity:
- One dep less. The retry logic is small (~30 LOC) and the surface area
  (1 decorator) doesn't justify a third-party dependency.
- Full control over which exceptions are retryable. Off-the-shelf
  retry libraries default to retry-everything which masks logic bugs
  (4xx besides 408/429) we WANT to surface immediately.

Usage:

    from catalyst_langgraph.clients._retry import retry_llm_call

    class FooClient:
        @retry_llm_call(name="nuextract")
        async def _call_llm(self, prompt: str) -> str:
            ...

The decorator logs each retry at WARNING so the bench TUI's log panel
surfaces "[nuextract] retry 1/3 after 1.4s: HTTP 500" — operator can
see degradation without grepping logs.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

# HTTP status codes that warrant a retry. Ollama's transient daemon
# hiccups manifest as 5xx; 408/429 are standard "back off and try again".
# 4xx besides these are logic errors (bad payload, missing model) — fail
# loud so the user fixes the call site instead of silently re-failing.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# httpx exception classes that indicate a transport-layer hiccup (TCP
# reset, partial response, connect timeout) rather than a server-side
# error. All are retryable.
_RETRYABLE_EXC: tuple[type[BaseException], ...] = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)

T = TypeVar("T")


def _is_retryable(exc: BaseException) -> bool:
    """True iff ``exc`` is one we should back off and retry on."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, _RETRYABLE_EXC)


def _backoff_seconds(attempt: int, *, base: float, cap: float) -> float:
    """Full-jitter exponential backoff: random.uniform(0, min(cap, base * 2**attempt)).

    AWS-style full-jitter avoids thundering-herd retries when many concurrent
    callers hit the same transient outage.  ``attempt`` is 0-indexed (the
    delay before retry #1 uses attempt=0 → max base*1).
    """
    return random.uniform(0, min(cap, base * (2**attempt)))


def retry_llm_call(
    *,
    name: str,
    attempts: int = 3,
    base: float = 1.0,
    cap: float = 30.0,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator: wrap an async LLM call with exponential-backoff retry.

    Args:
        name: Short label for log lines (e.g. ``"nuextract"``).
        attempts: Total attempts including the first call. Default 3 means
            at most 2 retries on transient failure.
        base: Backoff base in seconds. Default 1.0 → first retry sleeps
            in [0, 1.0], second in [0, 2.0], third in [0, 4.0].
        cap: Max backoff sleep (full-jitter is bounded by this).
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args, **kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(attempts):
                try:
                    return await fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001
                    if not _is_retryable(exc):
                        raise
                    last_exc = exc
                    if attempt + 1 >= attempts:
                        # Out of retries — propagate the final exception so
                        # the caller's error handling (StateInspector audit
                        # event, harness skip-to-next-doc) kicks in.
                        break
                    delay = _backoff_seconds(attempt, base=base, cap=cap)
                    logger.warning(
                        "[%s] retry %d/%d after %.1fs: %s",
                        name,
                        attempt + 1,
                        attempts - 1,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
            # All retries exhausted — re-raise the most recent exception.
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
