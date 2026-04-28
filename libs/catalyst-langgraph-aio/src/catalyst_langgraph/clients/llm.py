"""Async LLM client wrapping LangChain's ChatOpenAI."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

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

        self._chat_model = ChatOpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "unused",
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            timeout=self.timeout,
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
        """
        prompt_chars = sum(len(str(getattr(m, "content", m))) for m in messages)
        logger.info(
            "llm.structured_output: model=%s, schema=%s, method=%s, prompt_chars=%d",
            self.model,
            schema.__name__,
            self.structured_method,
            prompt_chars,
        )
        t0 = time.perf_counter()
        chain = self._chat_model.with_structured_output(schema, method=self.structured_method)
        result = await chain.ainvoke(messages)
        elapsed = time.perf_counter() - t0
        logger.info("llm.structured_output: done, schema=%s, duration=%.3fs", schema.__name__, elapsed)
        return result
