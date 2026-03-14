"""Reusable model selector widgets backed by live LiteLLM proxy discovery."""

from __future__ import annotations

import logging

import streamlit as st

from data_explorer.streamlit.llm_client import get_llm_client

logger = logging.getLogger(__name__)

_FALLBACK_CHAT_MODELS = ["gpt-4o-mini", "gpt-4o"]
_FALLBACK_EMBEDDING_MODELS = ["text-embedding-3-small", "text-embedding-3-large"]


@st.cache_data(ttl=300)
def _discover_chat_models() -> list[str]:
    """Fetch chat model list from proxy, falling back to defaults."""
    try:
        models = get_llm_client().list_chat_models()
        return models if models else _FALLBACK_CHAT_MODELS
    except Exception as exc:
        logger.warning("Failed to discover chat models: %s", exc)
        return _FALLBACK_CHAT_MODELS


@st.cache_data(ttl=300)
def _discover_embedding_models() -> list[str]:
    """Fetch embedding model list from proxy, falling back to defaults."""
    try:
        models = get_llm_client().list_embedding_models()
        return models if models else _FALLBACK_EMBEDDING_MODELS
    except Exception as exc:
        logger.warning("Failed to discover embedding models: %s", exc)
        return _FALLBACK_EMBEDDING_MODELS


def chat_model_selector(
    key: str = "chat_model",
    label: str = "Model",
) -> str:
    """Render a selectbox populated with live chat models from the proxy."""
    models = _discover_chat_models()
    return st.selectbox(label, models, index=0, key=key)


def embedding_model_selector(
    key: str = "embedding_model",
    label: str = "Embedding Model",
) -> str:
    """Render a selectbox populated with live embedding models from the proxy."""
    models = _discover_embedding_models()
    return st.selectbox(label, models, index=0, key=key)
