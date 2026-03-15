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
from typing import Any

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
    LLM_TOKENS_USED,
    track_duration,
)

logger = get_logger(__name__)


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

    base_url: str = os.environ.get(
        "LLM_BASE_URL", "https://api.openai.com/v1"
    )
    api_key: str = os.environ.get(
        "LLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")
    )
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
        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        LLM_REQUESTS.labels(model=self.model, operation="complete", status="pending").inc()
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
            logger.info("LLM complete done model=%s duration=%.2fs response_len=%d", self.model, duration, len(result))
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
        LLM_REQUESTS.labels(model=self.model, operation="complete_json", status="pending").inc()
        start = time.monotonic()
        try:
            with track_duration(LLM_REQUEST_DURATION, {"model": self.model, "operation": "complete_json"}):
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
            logger.info("LLM complete_json done model=%s duration=%.2fs response_len=%d", self.model, duration, len(result))
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
        logger.info("Initializing EmbeddingResource provider=%s model=%s", self.provider, self.model)
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
        """Embed a list of texts."""
        logger.info("Embedding %d texts with model=%s", len(texts), self.model)
        with track_duration(EMBEDDING_BATCH_DURATION, {"model": self.model}):
            result = self._embeddings.embed_documents(texts)
        EMBEDDING_VECTORS_CREATED.labels(model=self.model).inc(len(result))
        logger.info("Embedding complete count=%d dimensions=%d", len(result), len(result[0]) if result else 0)
        return result

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text string (uses query embedding for better retrieval)."""
        logger.debug("Embedding single text len=%d model=%s", len(text), self.model)
        result = self._embeddings.embed_query(text)
        EMBEDDING_VECTORS_CREATED.labels(model=self.model).inc(1)
        return result
