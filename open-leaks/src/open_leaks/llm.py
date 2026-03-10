"""LLM and embedding client abstractions.

Uses the OpenAI Python client for both OpenAI and Ollama backends.
Ollama exposes an OpenAI-compatible /v1 endpoint, so a single client works for both.
"""

from __future__ import annotations

from openai import OpenAI

from open_leaks.config import EmbeddingConfig, LLMConfig


class LLMClient:
    """Unified LLM client targeting OpenAI or Ollama via OpenAI-compatible API."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client = OpenAI(
            api_key=config.api_key or "ollama",
            base_url=config.base_url,
        )

    def complete(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        return response.choices[0].message.content or ""

    def complete_json(self, prompt: str, system: str = "") -> str:
        """Request JSON output from the model."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"


class EmbeddingClient:
    """Embedding client supporting sentence-transformers (local) or OpenAI API."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self._model = None
        self._openai_client = None

        if config.provider == "openai":
            self._openai_client = OpenAI(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url,
            )

    def _get_local_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.config.model)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.config.provider == "openai":
            response = self._openai_client.embeddings.create(
                model=self.config.model,
                input=texts,
            )
            return [item.embedding for item in response.data]

        model = self._get_local_model()
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def embed_single(self, text: str) -> list[float]:
        return self.embed([text])[0]
