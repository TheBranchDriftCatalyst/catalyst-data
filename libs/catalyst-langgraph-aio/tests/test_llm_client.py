"""Unit tests for LLMClient configuration (no network calls)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestLLMClientConfig:
    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    def test_explicit_params_override_env(self, mock_chat, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "env-model")
        monkeypatch.setenv("LLM_BASE_URL", "http://env-url")

        from catalyst_langgraph.clients.llm import LLMClient

        client = LLMClient(model="explicit-model", base_url="http://explicit-url")
        assert client.model == "explicit-model"
        assert client.base_url == "http://explicit-url"

    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    def test_defaults_when_no_env(self, mock_chat, monkeypatch):
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
        monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)

        from catalyst_langgraph.clients.llm import LLMClient

        client = LLMClient()
        assert client.model == "gpt-4o-mini"
        assert client.base_url == "https://api.openai.com/v1"
        assert client.temperature == 0.0
        assert client.max_tokens == 4096

    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    def test_api_key_falls_back_to_openai(self, mock_chat, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "openai-fallback-key")

        from catalyst_langgraph.clients.llm import LLMClient

        client = LLMClient()
        assert client.api_key == "openai-fallback-key"

    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    def test_env_vars_used_when_no_params(self, mock_chat, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "env-model")
        monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
        monkeypatch.setenv("LLM_MAX_TOKENS", "8192")

        from catalyst_langgraph.clients.llm import LLMClient

        client = LLMClient()
        assert client.model == "env-model"
        assert client.temperature == 0.7
        assert client.max_tokens == 8192
