"""Tests for EmbeddingResource provider dispatch.

Each provider is tested with a mock backend so no real API keys or local
models are needed.  The tests verify that:

1. ``setup_for_execution`` wires up the correct internal state for each provider.
2. ``embed()`` returns a list of vectors of the expected dimensionality.
3. ``embed_single()`` returns a single vector.
4. Provider-specific field defaults (base_url, api_key) are applied correctly.

``provider="local"`` is tested separately in ``test_embedding_local_qwen3.py``
to keep the slow / hardware-gated tests in one place.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")

import pytest

from dagster_io.llm import EmbeddingResource

# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_vectors(n: int, dim: int = 8) -> list[list[float]]:
    return [[float(i) / dim] * dim for i in range(n)]


def _mock_openai_embeddings(dim: int = 8) -> MagicMock:
    """Return a mock that quacks like OpenAIEmbeddings."""
    mock = MagicMock()
    mock.embed_documents.side_effect = lambda texts: _fake_vectors(len(texts), dim)
    mock.embed_query.side_effect = lambda text: _fake_vectors(1, dim)[0]
    return mock


def _make_resource(**kwargs: Any) -> EmbeddingResource:
    """Create an EmbeddingResource with test-friendly defaults."""
    defaults = {
        "provider": "openai",
        "api_key": "test-key",
        "model": "text-embedding-test",
        "batch_size": 100,
        "enable_cache": False,
    }
    defaults.update(kwargs)
    return EmbeddingResource(**defaults)


def _null_context() -> MagicMock:
    return MagicMock()


# ── openai provider ───────────────────────────────────────────────────────────


def test_openai_provider_setup_and_embed():
    resource = _make_resource(provider="openai", base_url="https://api.openai.com/v1")
    mock_emb = _mock_openai_embeddings(dim=8)

    with patch("dagster_io.llm.OpenAIEmbeddings", return_value=mock_emb):
        resource.setup_for_execution(_null_context())

    resource._embeddings = mock_emb
    result = resource.embed(["hello", "world"])

    assert len(result) == 2
    assert len(result[0]) == 8
    mock_emb.embed_documents.assert_called_once_with(["hello", "world"])


def test_openai_provider_embed_single():
    resource = _make_resource(provider="openai")
    mock_emb = _mock_openai_embeddings(dim=8)
    resource._embeddings = mock_emb

    result = resource.embed_single("single text")
    assert len(result) == 8
    mock_emb.embed_query.assert_called_once_with("single text")


# ── litellm provider ──────────────────────────────────────────────────────────


def test_litellm_provider_uses_litellm_base_url(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm-test:4000/v1")
    resource = _make_resource(
        provider="litellm",
        base_url="",  # defer to env var
        model="text-embedding-3-small",
        api_key="",  # no explicit key → falls back to "unused"
    )
    mock_emb = _mock_openai_embeddings()
    captured_kwargs: dict = {}

    def _capture(**kwargs):
        captured_kwargs.update(kwargs)
        return mock_emb

    with patch("dagster_io.llm.OpenAIEmbeddings", side_effect=_capture):
        resource.setup_for_execution(_null_context())

    assert captured_kwargs["base_url"] == "http://litellm-test:4000/v1"
    assert captured_kwargs["api_key"] == "unused"


def test_litellm_provider_default_base_url_when_env_missing(monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    resource = _make_resource(
        provider="litellm",
        base_url="",
    )
    captured_kwargs: dict = {}

    def _capture(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("dagster_io.llm.OpenAIEmbeddings", side_effect=_capture):
        resource.setup_for_execution(_null_context())

    assert captured_kwargs["base_url"] == "http://litellm:4000/v1"


def test_litellm_provider_explicit_base_url_takes_precedence(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://env-url/v1")
    resource = _make_resource(
        provider="litellm",
        base_url="http://explicit-url/v1",
    )
    captured_kwargs: dict = {}

    def _capture(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("dagster_io.llm.OpenAIEmbeddings", side_effect=_capture):
        resource.setup_for_execution(_null_context())

    assert captured_kwargs["base_url"] == "http://explicit-url/v1"


def test_litellm_embed_returns_vectors():
    resource = _make_resource(provider="litellm")
    mock_emb = _mock_openai_embeddings(dim=16)
    resource._embeddings = mock_emb

    result = resource.embed(["a", "b", "c"])
    assert len(result) == 3
    assert len(result[0]) == 16


# ── ollama provider ───────────────────────────────────────────────────────────


def test_ollama_provider_uses_ollama_base_url(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama-test:11434/v1")
    resource = _make_resource(
        provider="ollama",
        base_url="",
        model="nomic-embed-text",
    )
    captured_kwargs: dict = {}

    def _capture(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("dagster_io.llm.OpenAIEmbeddings", side_effect=_capture):
        resource.setup_for_execution(_null_context())

    assert captured_kwargs["base_url"] == "http://ollama-test:11434/v1"
    assert captured_kwargs["api_key"] == "ollama"


def test_ollama_provider_default_base_url(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    resource = _make_resource(
        provider="ollama",
        base_url="",
    )
    captured_kwargs: dict = {}

    def _capture(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("dagster_io.llm.OpenAIEmbeddings", side_effect=_capture):
        resource.setup_for_execution(_null_context())

    assert captured_kwargs["base_url"] == "http://localhost:11434/v1"


def test_ollama_embed_returns_two_vectors():
    resource = _make_resource(provider="ollama")
    mock_emb = _mock_openai_embeddings(dim=768)
    resource._embeddings = mock_emb

    result = resource.embed(["hello", "world"])
    assert len(result) == 2
    assert len(result[0]) == 768


# ── huggingface provider (deprecated) ────────────────────────────────────────


def test_huggingface_provider_logs_deprecation_warning(caplog):
    """The huggingface provider emits a deprecation warning before delegating.

    We patch the lazy import inside setup_for_execution so that langchain-huggingface
    doesn't need to be installed for this unit test.
    """
    import logging
    import sys
    from types import ModuleType

    resource = _make_resource(provider="huggingface", model="sentence-transformers/all-MiniLM-L6-v2")
    mock_emb = MagicMock()

    # Inject a minimal stub for the optional langchain_huggingface module so the
    # lazy import inside setup_for_execution succeeds without the real package.
    stub_module = ModuleType("langchain_huggingface")
    stub_module.HuggingFaceEmbeddings = MagicMock(return_value=mock_emb)  # type: ignore[attr-defined]
    with (
        patch.dict(sys.modules, {"langchain_huggingface": stub_module}),
        caplog.at_level(logging.WARNING, logger="dagster_io.llm"),
    ):
        resource.setup_for_execution(_null_context())

    deprecation_messages = [r for r in caplog.records if "deprecated" in r.message.lower()]
    assert len(deprecation_messages) >= 1, "Expected a deprecation warning for provider='huggingface'"


# ── instruction prefix ────────────────────────────────────────────────────────


def test_instruction_prefix_applied_in_embed_local():
    """When instruction is set, texts should be prefixed before encoding."""
    resource = _make_resource(
        provider="local",
        model="mock-model",
        instruction="Find relevant documents",
        enable_cache=False,
    )
    captured_inputs: list[list[str]] = []

    def _mock_encode(texts, **kwargs):
        import numpy as np

        captured_inputs.append(list(texts))
        return np.ones((len(texts), 4), dtype="float32")

    mock_st = MagicMock()
    mock_st.encode.side_effect = _mock_encode
    mock_st.get_sentence_embedding_dimension.return_value = 4
    resource._st_model = mock_st

    resource._embed_local(["query text"])

    assert len(captured_inputs) == 1
    assert captured_inputs[0][0].startswith("Instruct: Find relevant documents\nQuery: ")
    assert "query text" in captured_inputs[0][0]


def test_no_instruction_no_prefix():
    """When instruction is None, texts pass through unchanged."""
    resource = _make_resource(
        provider="local",
        model="mock-model",
        instruction=None,
        enable_cache=False,
    )
    captured_inputs: list[list[str]] = []

    def _mock_encode(texts, **kwargs):
        import numpy as np

        captured_inputs.append(list(texts))
        return np.ones((len(texts), 4), dtype="float32")

    mock_st = MagicMock()
    mock_st.encode.side_effect = _mock_encode
    mock_st.get_sentence_embedding_dimension.return_value = 4
    resource._st_model = mock_st

    resource._embed_local(["plain text"])

    assert captured_inputs[0][0] == "plain text"


# ── matryoshka truncation ─────────────────────────────────────────────────────


def test_matryoshka_truncation_reduces_dim():
    """_embed_local truncates vectors to self.dimensions and re-normalises."""
    import numpy as np

    resource = _make_resource(
        provider="local",
        model="mock-model",
        dimensions=2,
        enable_cache=False,
    )

    def _mock_encode(texts, **kwargs):
        # Return 4-dim vectors; should be truncated to 2
        return np.array([[1.0, 2.0, 3.0, 4.0]] * len(texts), dtype="float32")

    mock_st = MagicMock()
    mock_st.encode.side_effect = _mock_encode
    mock_st.get_sentence_embedding_dimension.return_value = 4
    resource._st_model = mock_st

    result = resource._embed_local(["text"])
    assert len(result[0]) == 2
    # Should be normalised (L2 norm ≈ 1)
    norm = sum(x * x for x in result[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-5


def test_no_truncation_when_dim_matches_native():
    """If target_dim equals native dim, no truncation occurs."""
    import numpy as np

    resource = _make_resource(
        provider="local",
        model="mock-model",
        dimensions=4,
        enable_cache=False,
    )

    def _mock_encode(texts, **kwargs):
        return np.array([[1.0, 0.0, 0.0, 0.0]] * len(texts), dtype="float32")

    mock_st = MagicMock()
    mock_st.encode.side_effect = _mock_encode
    mock_st.get_sentence_embedding_dimension.return_value = 4
    resource._st_model = mock_st

    result = resource._embed_local(["text"])
    assert len(result[0]) == 4


# ── local provider missing dep error ─────────────────────────────────────────


def test_local_provider_raises_friendly_error_when_deps_missing(monkeypatch):
    """provider='local' raises ImportError with install instructions when sentence-transformers is absent."""
    resource = _make_resource(provider="local")

    import builtins

    real_import = builtins.__import__

    def _block_torch(name, *args, **kwargs):
        if name in ("torch", "sentence_transformers"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_block_torch), pytest.raises(ImportError, match="local-embed"):
        resource.setup_for_execution(_null_context())


# ── batch size defaults ───────────────────────────────────────────────────────


def test_local_provider_default_batch_size_is_16():
    """Default batch size for local provider is 16."""
    resource = _make_resource(provider="local", batch_size=None, enable_cache=False)
    captured_kwargs: dict = {}

    def _mock_encode(texts, **kwargs):
        import numpy as np

        captured_kwargs.update(kwargs)
        return np.ones((len(texts), 4), dtype="float32")

    mock_st = MagicMock()
    mock_st.encode.side_effect = _mock_encode
    mock_st.get_sentence_embedding_dimension.return_value = 4
    resource._st_model = mock_st

    resource._embed_local(["text"])
    assert captured_kwargs.get("batch_size") == 16
