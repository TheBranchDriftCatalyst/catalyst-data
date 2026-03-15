"""Shared LLM and embedding resources for Dagster pipelines.

Built on LangChain so every code location gets:
- ChatOpenAI for completions (works with LiteLLM proxy, Ollama, vLLM, OpenAI)
- OpenAIEmbeddings for vector embeddings (same backend flexibility)
- Optional HuggingFace local embeddings via ``dagster-io[huggingface]``

Configure via environment variables or Dagster launchpad.
"""

from __future__ import annotations

import os
from typing import Any

from dagster import ConfigurableResource
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, PrivateAttr


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
        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        response = self._chat_model.invoke(messages)
        return str(response.content)

    def complete_json(self, prompt: str, *, system: str = "") -> str:
        """Send a chat completion requesting JSON output."""
        model = self._chat_model.bind(response_format={"type": "json_object"})
        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        response = model.invoke(messages)
        return str(response.content)

    def with_structured_output(self, schema: type[BaseModel]) -> Any:
        """Return a LangChain runnable that outputs a Pydantic model.

        Usage::

            class Entities(BaseModel):
                entities: list[Entity]

            chain = llm.with_structured_output(Entities)
            result = chain.invoke([HumanMessage(content="Extract entities...")])
        """
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
        return self._embeddings.embed_documents(texts)

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text string (uses query embedding for better retrieval)."""
        return self._embeddings.embed_query(text)
