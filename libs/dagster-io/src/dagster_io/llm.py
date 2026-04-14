"""Shared LLM and embedding resources for Dagster pipelines.

Built on LangChain so every code location gets:
- ChatOpenAI for completions (works with LiteLLM proxy, Ollama, vLLM, OpenAI)
- OpenAIEmbeddings for vector embeddings (same backend flexibility)
- Optional HuggingFace local embeddings via ``dagster-io[huggingface]``

Configure via environment variables or Dagster launchpad.
"""

from __future__ import annotations

import os
import time
import unicodedata
from collections.abc import Callable
from typing import Any, TypeVar

from dagster import ConfigurableResource
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, PrivateAttr

from dagster_io.logging import get_logger
from dagster_io.metrics import (
    EMBEDDING_BATCH_DURATION,
    EMBEDDING_VECTORS_CREATED,
    LLM_REQUEST_DURATION,
    LLM_REQUESTS,
    LLM_TOKENS_CACHED_TOTAL,
    LLM_TOKENS_USED,
    track_duration,
)


def _extract_cached_tokens(usage: object) -> int:
    """Pull prompt-cache-hit token count out of a LangChain UsageMetadata.

    Current langchain-core surfaces prompt cache stats under
    ``usage_metadata["input_token_details"]["cache_read"]`` (nested dict).
    Older langchain-openai / LiteLLM passthroughs sometimes expose a flat
    ``cache_read_input_tokens`` key. We probe both so the metric works
    regardless of which shape the backend returns, and return 0 if neither
    path is populated (models that don't support prompt caching).
    """
    # Nested path: usage_metadata.input_token_details.cache_read
    details: object | None = (
        usage.get("input_token_details")  # type: ignore[union-attr]
        if hasattr(usage, "get")
        else getattr(usage, "input_token_details", None)
    )
    if details is not None:
        nested = (
            details.get("cache_read", 0)  # type: ignore[union-attr]
            if hasattr(details, "get")
            else getattr(details, "cache_read", 0)
        )
        if nested:
            return int(nested)
    # Legacy flat path: usage_metadata.cache_read_input_tokens
    flat = (
        usage.get("cache_read_input_tokens", 0)  # type: ignore[union-attr]
        if hasattr(usage, "get")
        else getattr(usage, "cache_read_input_tokens", 0)
    )
    return int(flat or 0)


logger = get_logger(__name__)


def _normalize_text(text: str) -> str:
    """Normalize text for LLM APIs — NFKC normalization + control char removal.

    NFKC converts fullwidth characters to ASCII equivalents (e.g., ： → :, ｜ → |),
    decomposes ligatures, and normalizes compatible forms. This prevents JSON
    serialization issues with litellm/OpenAI APIs that choke on fullwidth Unicode.
    """
    # NFKC: fullwidth → ASCII, ligatures decomposed, compatible forms normalized
    text = unicodedata.normalize("NFKC", text)
    # Strip null bytes and other control chars (except newline/tab)
    text = "".join(c for c in text if c >= " " or c in "\n\r\t")
    return text


def _normalize_messages(messages: list) -> list:
    """Normalize all text content in a message list before sending to LLM."""
    normalized = []
    for msg in messages:
        if hasattr(msg, "content") and isinstance(msg.content, str):
            normalized.append(msg.__class__(content=_normalize_text(msg.content)))
        else:
            normalized.append(msg)
    return normalized

T = TypeVar("T")


class LLMResource(ConfigurableResource):
    """LLM resource shared across all code locations.

    Wraps LangChain's ChatOpenAI, which targets any OpenAI-compatible endpoint
    (LiteLLM proxy, Ollama, vLLM, direct OpenAI).

    Usage in assets::

        @asset
        def my_asset(llm: LLMResource):
            result = llm.complete("Summarize this text: ...")
            structured = llm.complete_json("Extract entities as JSON: ...")
            # Or get the underlying LangChain model for chains:
            chat_model = llm.get_model()
    """

    base_url: str = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    api_key: str = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    model: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    temperature: float = float(os.environ.get("LLM_TEMPERATURE", "0.0"))
    max_tokens: int = int(os.environ.get("LLM_MAX_TOKENS", "4096"))

    _chat_model: ChatOpenAI = PrivateAttr()

    max_retries: int = int(os.environ.get("LLM_MAX_RETRIES", "5"))
    request_timeout: float = float(os.environ.get("LLM_REQUEST_TIMEOUT", "300"))

    def setup_for_execution(self, context) -> None:  # noqa: ANN001
        logger.info("Initializing LLM resource model=%s base_url=%s", self.model, self.base_url)
        self._chat_model = ChatOpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "unused",
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            timeout=self.request_timeout,
        )

    def get_model(self) -> BaseChatModel:
        """Return the underlying LangChain chat model for use in chains."""
        return self._chat_model

    def complete(self, prompt: str, *, system: str = "") -> str:
        """Send a chat completion and return the text response."""
        logger.debug("LLM complete model=%s prompt_len=%d", self.model, len(prompt))
        messages = _normalize_messages([
            *([SystemMessage(content=system)] if system else []),
            HumanMessage(content=prompt),
        ])
        # Note: LLM_REQUESTS is only incremented on terminal states (success or
        # error). A previous in-flight status increment at request start was
        # never paired with a decrement, so it grew monotonically and the
        # Grafana piechart was dominated by historical starts rather than
        # current in-flight requests. If we ever need in-flight visibility,
        # add a proper Gauge (inc at start, dec in finally) — don't abuse the
        # terminal-state counter.
        start = time.monotonic()
        try:
            with track_duration(LLM_REQUEST_DURATION, {"model": self.model, "operation": "complete"}):
                response = self._chat_model.invoke(messages)
            duration = time.monotonic() - start
            result = str(response.content)
            LLM_REQUESTS.labels(model=self.model, operation="complete", status="success").inc()
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage = response.usage_metadata
                if hasattr(usage, "get"):
                    prompt_tokens = usage.get("input_tokens", 0)
                    completion_tokens = usage.get("output_tokens", 0)
                else:
                    prompt_tokens = getattr(usage, "input_tokens", 0)
                    completion_tokens = getattr(usage, "output_tokens", 0)
                LLM_TOKENS_USED.labels(model=self.model, token_type="prompt").inc(prompt_tokens)
                LLM_TOKENS_USED.labels(model=self.model, token_type="completion").inc(completion_tokens)
                cached_tokens = _extract_cached_tokens(usage)
                if cached_tokens:
                    LLM_TOKENS_CACHED_TOTAL.labels(model=self.model).inc(cached_tokens)
            logger.info(
                "LLM complete done model=%s duration=%.2fs response_len=%d",
                self.model,
                duration,
                len(result),
            )
            return result
        except Exception:
            LLM_REQUESTS.labels(model=self.model, operation="complete", status="error").inc()
            logger.error("LLM complete failed model=%s", self.model, exc_info=True)
            raise

    def complete_json(self, prompt: str, *, system: str = "") -> str:
        """Send a chat completion requesting JSON output."""
        logger.debug("LLM complete_json model=%s prompt_len=%d", self.model, len(prompt))
        model = self._chat_model.bind(response_format={"type": "json_object"})
        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        # See `complete()` above for rationale — no `pending` emission; this
        # counter tracks terminal states only.
        start = time.monotonic()
        try:
            with track_duration(
                LLM_REQUEST_DURATION,
                {"model": self.model, "operation": "complete_json"},
            ):
                response = model.invoke(messages)
            duration = time.monotonic() - start
            result = str(response.content)
            LLM_REQUESTS.labels(model=self.model, operation="complete_json", status="success").inc()
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage = response.usage_metadata
                if hasattr(usage, "get"):
                    prompt_tokens = usage.get("input_tokens", 0)
                    completion_tokens = usage.get("output_tokens", 0)
                else:
                    prompt_tokens = getattr(usage, "input_tokens", 0)
                    completion_tokens = getattr(usage, "output_tokens", 0)
                LLM_TOKENS_USED.labels(model=self.model, token_type="prompt").inc(prompt_tokens)
                LLM_TOKENS_USED.labels(model=self.model, token_type="completion").inc(completion_tokens)
                cached_tokens = _extract_cached_tokens(usage)
                if cached_tokens:
                    LLM_TOKENS_CACHED_TOTAL.labels(model=self.model).inc(cached_tokens)
            logger.info(
                "LLM complete_json done model=%s duration=%.2fs response_len=%d",
                self.model,
                duration,
                len(result),
            )
            return result
        except Exception:
            LLM_REQUESTS.labels(model=self.model, operation="complete_json", status="error").inc()
            logger.error("LLM complete_json failed model=%s", self.model, exc_info=True)
            raise

    def with_structured_output(self, schema: type[BaseModel]) -> Any:
        """Return a LangChain runnable that outputs a Pydantic model.

        Usage::

            class Entities(BaseModel):
                entities: list[Entity]

            chain = llm.with_structured_output(Entities)
            result = chain.invoke([HumanMessage(content="Extract entities...")])
        """
        logger.debug("LLM with_structured_output model=%s schema=%s", self.model, schema.__name__)
        return self._chat_model.with_structured_output(schema)

    def invoke_batch(
        self,
        chain: Any,
        messages_fn: Callable[[T], list],
        items: list[T],
        *,
        log_every: int = 50,
        operation: str = "batch",
    ) -> list[Any]:
        """Invoke a chain over a list of items with progress logging.

        Args:
            chain: LangChain runnable (e.g. from with_structured_output).
            messages_fn: Function that takes an item and returns a messages list.
            items: Items to process.
            log_every: Log progress every N items.
            operation: Label for metrics/logs.
        """
        logger.info("LLM %s starting: %d items, model=%s", operation, len(items), self.model)
        results = []
        for i, item in enumerate(items):
            with track_duration(LLM_REQUEST_DURATION, {"model": self.model, "operation": operation}):
                result = chain.invoke(_normalize_messages(messages_fn(item)))
            results.append(result)
            LLM_REQUESTS.labels(model=self.model, operation=operation, status="success").inc()

            if (i + 1) % log_every == 0 or (i + 1) == len(items):
                logger.info(
                    "LLM %s progress: %d/%d (%.0f%%)",
                    operation,
                    i + 1,
                    len(items),
                    (i + 1) / len(items) * 100,
                )
        return results


class EmbeddingResource(ConfigurableResource):
    """Embedding resource shared across all code locations.

    Uses LangChain's OpenAIEmbeddings by default (works with LiteLLM proxy,
    Ollama, vLLM, OpenAI). Set ``provider="huggingface"`` for local
    sentence-transformers (requires ``dagster-io[huggingface]``).

    Usage in assets::

        @asset
        def my_asset(embeddings: EmbeddingResource):
            vectors = embeddings.embed(["hello world", "another doc"])
    """

    provider: str = os.environ.get("EMBEDDING_PROVIDER", "openai")
    base_url: str = os.environ.get(
        "EMBEDDING_BASE_URL",
        os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
    )
    api_key: str = os.environ.get(
        "EMBEDDING_API_KEY",
        os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
    )
    model: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    dimensions: int = int(os.environ.get("EMBEDDING_DIMENSIONS", "1536"))
    batch_size: int = int(os.environ.get("EMBEDDING_BATCH_SIZE", "100"))

    _embeddings: Any = PrivateAttr()

    def setup_for_execution(self, context) -> None:  # noqa: ANN001
        logger.info(
            "Initializing EmbeddingResource provider=%s model=%s",
            self.provider,
            self.model,
        )
        if self.provider == "huggingface":
            from langchain_huggingface import HuggingFaceEmbeddings

            self._embeddings = HuggingFaceEmbeddings(
                model_name=self.model,
            )
        else:
            self._embeddings = OpenAIEmbeddings(
                base_url=self.base_url,
                api_key=self.api_key or "unused",
                model=self.model,
                chunk_size=self.batch_size,
            )

    def get_embeddings(self) -> Any:
        """Return the underlying LangChain embeddings model for use in chains."""
        return self._embeddings

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, processing internally in batches with progress logging."""
        logger.info(
            "Embedding %d texts with model=%s (batch_size=%d)",
            len(texts),
            self.model,
            self.batch_size,
        )
        all_vectors: list[list[float]] = []
        for batch_start in range(0, len(texts), self.batch_size):
            batch = texts[batch_start : batch_start + self.batch_size]
            with track_duration(
                EMBEDDING_BATCH_DURATION,
                {"provider": self.provider, "model": self.model},
            ):
                vectors = self._embeddings.embed_documents(batch)
            all_vectors.extend(vectors)
            EMBEDDING_VECTORS_CREATED.labels(provider=self.provider, model=self.model).inc(len(vectors))
            processed = min(batch_start + self.batch_size, len(texts))
            logger.info(
                "Embedding progress: %d/%d texts (%.0f%%)",
                processed,
                len(texts),
                processed / len(texts) * 100,
            )
        logger.info(
            "Embedding complete count=%d dimensions=%d",
            len(all_vectors),
            len(all_vectors[0]) if all_vectors else 0,
        )
        return all_vectors

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text string (uses query embedding for better retrieval)."""
        logger.debug("Embedding single text len=%d model=%s", len(text), self.model)
        result = self._embeddings.embed_query(text)
        EMBEDDING_VECTORS_CREATED.labels(provider=self.provider, model=self.model).inc(1)
        return result
