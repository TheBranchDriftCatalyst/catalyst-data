"""Unit tests for LLMClient configuration and structured output recovery (no network calls)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel


class SimpleSchema(BaseModel):
    """Minimal schema for testing structured output parsing."""

    mentions: list[str] = []


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
        assert client.max_tokens == 16384

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


class TestStructuredOutputThinkTagRecovery:
    """Test that structured_output recovers from <think>-tagged LLM output."""

    @pytest.mark.asyncio
    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    async def test_clean_json_passes_through(self, mock_chat_cls):
        """When the parser succeeds, return the parsed result directly."""
        from catalyst_langgraph.clients.llm import LLMClient

        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = {
            "raw": MagicMock(content='{"mentions": ["Alice"]}'),
            "parsed": SimpleSchema(mentions=["Alice"]),
            "parsing_error": None,
        }
        mock_chat_cls.return_value.with_structured_output.return_value = mock_chain

        client = LLMClient()
        from langchain_core.messages import HumanMessage

        result = await client.structured_output(SimpleSchema, [HumanMessage(content="test")])
        assert result.mentions == ["Alice"]

    @pytest.mark.asyncio
    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    async def test_think_tags_recovered(self, mock_chat_cls):
        """When parser fails due to <think> tags, strip them and re-parse."""
        from catalyst_langgraph.clients.llm import LLMClient

        raw_content = '<think>\nLet me analyze the text...\n</think>\n{"mentions": ["Bob", "Carol"]}'
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = {
            "raw": MagicMock(content=raw_content),
            "parsed": None,
            "parsing_error": "Invalid json output",
        }
        mock_chat_cls.return_value.with_structured_output.return_value = mock_chain

        client = LLMClient()
        from langchain_core.messages import HumanMessage

        result = await client.structured_output(SimpleSchema, [HumanMessage(content="test")])
        assert result.mentions == ["Bob", "Carol"]

    @pytest.mark.asyncio
    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    async def test_think_tags_with_code_fences_recovered(self, mock_chat_cls):
        """Recovery handles both think tags and code fences."""
        from catalyst_langgraph.clients.llm import LLMClient

        raw_content = '<think>\nReasoning here.\n</think>\n```json\n{"mentions": ["Dave"]}\n```'
        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = {
            "raw": MagicMock(content=raw_content),
            "parsed": None,
            "parsing_error": "Invalid json output",
        }
        mock_chat_cls.return_value.with_structured_output.return_value = mock_chain

        client = LLMClient()
        from langchain_core.messages import HumanMessage

        result = await client.structured_output(SimpleSchema, [HumanMessage(content="test")])
        assert result.mentions == ["Dave"]

    @pytest.mark.asyncio
    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    async def test_unparseable_raises_valueerror(self, mock_chat_cls):
        """When recovery also fails, raise ValueError with diagnostics."""
        from catalyst_langgraph.clients.llm import LLMClient

        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = {
            "raw": MagicMock(content="totally not json at all"),
            "parsed": None,
            "parsing_error": "Invalid json output",
        }
        mock_chat_cls.return_value.with_structured_output.return_value = mock_chain

        client = LLMClient()
        from langchain_core.messages import HumanMessage

        with pytest.raises(ValueError, match="Structured output parsing failed"):
            await client.structured_output(SimpleSchema, [HumanMessage(content="test")])

    @pytest.mark.asyncio
    @patch("catalyst_langgraph.clients.llm.ChatOpenAI")
    async def test_no_raw_text_raises_valueerror(self, mock_chat_cls):
        """When no raw text is available, raise ValueError."""
        from catalyst_langgraph.clients.llm import LLMClient

        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = {
            "raw": None,
            "parsed": None,
            "parsing_error": "Something went wrong",
        }
        mock_chat_cls.return_value.with_structured_output.return_value = mock_chain

        client = LLMClient()
        from langchain_core.messages import HumanMessage

        with pytest.raises(ValueError, match="no raw text available"):
            await client.structured_output(SimpleSchema, [HumanMessage(content="test")])
