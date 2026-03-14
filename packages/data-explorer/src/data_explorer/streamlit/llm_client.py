"""Lightweight LLM client for the Streamlit data explorer app.

Talks to a LiteLLM proxy using the OpenAI SDK.  Provides chat completions,
streaming, embeddings, and RAG helper utilities.
"""

from __future__ import annotations

import logging
import time
from typing import Generator

import streamlit as st

from data_explorer.streamlit.config import LLMConfig, get_llm_config

logger = logging.getLogger(__name__)

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

_OPENAI_MISSING_MSG = (
    "The 'openai' package is required for LLMClient but is not installed. "
    "Install it with:  pip install openai"
)


class LLMClient:
    """Thin wrapper around the OpenAI SDK pointed at a LiteLLM proxy."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        if config is None:
            config = get_llm_config()
        self.config = config

        if openai is None:
            raise ImportError(_OPENAI_MISSING_MSG)

        self._client = openai.OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )

    # --------------------------------------------------------------------- #
    # Chat completions
    # --------------------------------------------------------------------- #

    def chat(
        self,
        messages: list[dict],
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> str:
        """Single (non-streaming) chat completion with retry logic."""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Chat completion attempt %d failed: %s", attempt + 1, exc
                )
                time.sleep(2**attempt)

        raise RuntimeError(
            f"Chat completion failed after 3 attempts: {last_exc}"
        ) from last_exc

    def stream_chat(
        self,
        messages: list[dict],
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> Generator[str, None, None]:
        """Streaming chat completion -- yields content chunks as they arrive."""
        stream = self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    # --------------------------------------------------------------------- #
    # Embeddings
    # --------------------------------------------------------------------- #

    def embed(self, text: str) -> list[float]:
        """Embed a single text string using the configured embedding model."""
        response = self._client.embeddings.create(
            model=self.config.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    def embed_batch(
        self, texts: list[str], batch_size: int = 100
    ) -> list[list[float]]:
        """Embed multiple texts, batching requests to avoid payload limits."""
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = self._client.embeddings.create(
                model=self.config.embedding_model,
                input=batch,
            )
            all_embeddings.extend([item.embedding for item in response.data])
        return all_embeddings


# ------------------------------------------------------------------------- #
# RAG helpers
# ------------------------------------------------------------------------- #

_DEFAULT_RAG_SYSTEM_PROMPT = (
    "You are a research assistant. Answer the question based ONLY on the "
    "provided context. If the context doesn't contain enough information, "
    "say so. Cite sources using [Source: document_id] format."
)


def build_rag_context(chunks: list[dict], max_chars: int = 8000) -> str:
    """Format retrieved chunks with source attribution for LLM context."""
    parts: list[str] = []
    char_count = 0
    for chunk in chunks:
        document_id = chunk.get("document_id", "unknown")
        index = chunk.get("index", "?")
        text = chunk.get("text", "")
        block = f"[Source: {document_id} | Chunk {index}]\n{text}\n---"
        if char_count + len(block) > max_chars:
            break
        parts.append(block)
        char_count += len(block)
    return "\n".join(parts)


def answer_with_context(
    client: LLMClient,
    question: str,
    chunks: list[dict],
    system_prompt: str | None = None,
) -> str:
    """Build RAG context from *chunks* and send to the LLM."""
    context = build_rag_context(chunks)
    system = system_prompt or _DEFAULT_RAG_SYSTEM_PROMPT

    messages: list[dict] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {question}",
        },
    ]
    return client.chat(messages)


# ------------------------------------------------------------------------- #
# Cached singleton
# ------------------------------------------------------------------------- #


@st.cache_resource
def get_llm_client() -> LLMClient:
    """Return a cached :class:`LLMClient` instance (one per Streamlit app)."""
    return LLMClient()
