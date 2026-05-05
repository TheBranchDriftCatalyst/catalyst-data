"""Async LLM client wrapping LangChain's ChatOpenAI."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from catalyst_langgraph.prompts import strip_code_fences, strip_think_blocks

logger = logging.getLogger(__name__)


class LLMClient:
    """Async wrapper around ChatOpenAI.

    Config from environment variables:
    - LLM_BASE_URL (default: https://api.openai.com/v1)
    - LLM_API_KEY / OPENAI_API_KEY
    - LLM_MODEL (default: gpt-4o-mini)
    - LLM_TEMPERATURE (default: 0.0)
    - LLM_MAX_TOKENS (default: 16384)
    - LLM_MAX_RETRIES (default: 5)
    - LLM_TIMEOUT (default: 300)
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
        timeout: int | None = None,
        structured_method: str | None = None,
    ) -> None:
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.temperature = temperature if temperature is not None else float(os.environ.get("LLM_TEMPERATURE", "0.0"))
        self.max_tokens = max_tokens if max_tokens is not None else int(os.environ.get("LLM_MAX_TOKENS", "16384"))
        self.max_retries = max_retries if max_retries is not None else int(os.environ.get("LLM_MAX_RETRIES", "5"))
        self.timeout = timeout if timeout is not None else int(os.environ.get("LLM_TIMEOUT", "300"))
        # "function_calling" (default for OpenAI/vLLM), "json_mode" (for models
        # without tool support like nuextract), or "json_schema".
        self.structured_method = structured_method or os.environ.get("LLM_STRUCTURED_METHOD", "function_calling")

        # CD-azmn: use httpx.Timeout so a stalled READ on a wedged Ollama
        # connection trips after self.timeout seconds instead of hanging
        # forever in S+ on a TCP-established-but-dead socket.
        self._chat_model = ChatOpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "unused",
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            timeout=httpx.Timeout(connect=10.0, read=float(self.timeout), write=10.0, pool=10.0),
        )

    async def complete(self, prompt: str, *, system: str = "") -> str:
        """Send a chat completion and return the text response."""
        messages: list[Any] = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))

        prompt_chars = sum(len(str(m.content)) for m in messages)
        logger.info("llm.complete: model=%s, prompt_chars=%d", self.model, prompt_chars)
        t0 = time.perf_counter()
        response = await self._chat_model.ainvoke(messages)
        elapsed = time.perf_counter() - t0
        logger.info("llm.complete: done, response_len=%d, duration=%.3fs", len(str(response.content)), elapsed)
        return str(response.content)

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        """Invoke with structured output, returning a Pydantic model instance.

        Uses self.structured_method to select the extraction strategy:
        - "function_calling" (default): OpenAI tool/function calling
        - "json_mode": response_format=json_object (for models without tool support)
        - "json_schema": response_format=json_schema (OpenAI strict mode)

        Handles reasoning models (DeepSeek-R1, Qwen3 thinking) that prepend
        ``<think>...</think>`` blocks before their JSON output, which breaks
        LangChain's built-in parser.

        SPO bench capture (CD Gap #5): when the calling thread has an open
        ``dagster_io.bench.spo_capture`` slot, we lazily write the raw
        response text + usage metadata + parsing_error into it before
        returning. Non-SPO callers leave the slot ``None`` and pay no
        overhead.
        """
        prompt_chars = sum(len(str(getattr(m, "content", m))) for m in messages)
        logger.info(
            "llm.structured_output: model=%s, schema=%s, method=%s, prompt_chars=%d",
            self.model,
            schema.__name__,
            self.structured_method,
            prompt_chars,
        )
        # Lazy import: the bench capture module lives in dagster_io and
        # we don't want to hard-couple catalyst-langgraph-aio to it.
        # ``is_capturing()`` returns False when the module is absent or
        # no thread-local slot is open, so the hot path stays clean.
        try:
            from dagster_io.bench import spo_capture  # type: ignore[import-not-found]

            _capture_active = spo_capture.is_capturing()
        except Exception:
            spo_capture = None  # type: ignore[assignment]
            _capture_active = False

        t0 = time.perf_counter()
        chain = self._chat_model.with_structured_output(
            schema,
            method=self.structured_method,
            include_raw=True,
        )
        raw_result = await chain.ainvoke(messages)
        elapsed = time.perf_counter() - t0

        parsed = raw_result.get("parsed")
        parsing_error = raw_result.get("parsing_error")

        # Pull raw text + usage off the AIMessage. ``raw`` is present
        # whenever ``include_raw=True`` (always, here); ``usage_metadata``
        # may be missing on some adapters / vLLM builds — tolerate that.
        if _capture_active and spo_capture is not None:
            raw_msg = raw_result.get("raw")
            raw_text = str(raw_msg.content) if raw_msg is not None and hasattr(raw_msg, "content") else ""
            usage_meta = getattr(raw_msg, "usage_metadata", None) or {}
            usage: dict[str, int] = {}
            if isinstance(usage_meta, dict):
                # LangChain normalises to (input_tokens, output_tokens, total_tokens);
                # OpenAI raw uses (prompt_tokens, completion_tokens, total_tokens).
                # Accept both shapes — emit OpenAI keys downstream for stability.
                tokens_in = int(usage_meta.get("input_tokens") or usage_meta.get("prompt_tokens") or 0)
                tokens_out = int(usage_meta.get("output_tokens") or usage_meta.get("completion_tokens") or 0)
                tokens_total = int(usage_meta.get("total_tokens") or (tokens_in + tokens_out))
                if tokens_in or tokens_out or tokens_total:
                    usage = {
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "tokens_total": tokens_total,
                    }
            spo_capture.write(raw_text, usage=usage, parsing_error=parsing_error)

        if parsed is not None:
            logger.info("llm.structured_output: done, schema=%s, duration=%.3fs", schema.__name__, elapsed)
            return parsed

        # Parsing failed — try to recover from <think>-tagged or
        # code-fenced output that the default parser can't handle.
        raw_msg = raw_result.get("raw")
        raw_text = str(raw_msg.content) if raw_msg and hasattr(raw_msg, "content") else ""

        if not raw_text:
            raise ValueError(
                f"Structured output parsing failed for {schema.__name__} "
                f"and no raw text available. Error: {parsing_error}"
            )

        logger.warning(
            "llm.structured_output: parser failed, attempting think-tag/fence stripping. raw_len=%d, error=%s",
            len(raw_text),
            parsing_error,
        )

        # Strip <think> blocks, then code fences, then try JSON parse
        cleaned = strip_think_blocks(raw_text)
        cleaned = strip_code_fences(cleaned)

        try:
            data = json.loads(cleaned)
            result = schema.model_validate(data)
            logger.info(
                "llm.structured_output: recovered via stripping, schema=%s, duration=%.3fs",
                schema.__name__,
                elapsed,
            )
            return result
        except (json.JSONDecodeError, Exception) as e:
            raise ValueError(
                f"Structured output parsing failed for {schema.__name__}. "
                f"Original error: {parsing_error}. "
                f"Recovery error: {e}. "
                f"Cleaned text: {cleaned[:500]}"
            ) from e
