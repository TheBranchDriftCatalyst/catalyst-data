"""Dagster configuration for open-leaks pipeline."""

import os
import tempfile

from dagster import Config


class LLMConfig(Config):
    """LLM provider configuration.

    Uses OpenAI-compatible API for both OpenAI and Ollama backends.
    Set LLM_PROVIDER=ollama and OLLAMA_URL to use local Ollama.
    """

    provider: str = os.environ.get("LLM_PROVIDER", "openai")
    model: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    api_key: str = os.environ.get("OPENAI_API_KEY", "")
    base_url: str = os.environ.get(
        "LLM_BASE_URL",
        os.environ.get("OLLAMA_URL", "https://api.openai.com/v1"),
    )
    temperature: float = 0.0
    max_tokens: int = 4096


class EmbeddingConfig(Config):
    """Embedding provider configuration."""

    provider: str = os.environ.get("EMBEDDING_PROVIDER", "sentence-transformers")
    model: str = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    dimensions: int = int(os.environ.get("EMBEDDING_DIMENSIONS", "384"))
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url: str = os.environ.get("EMBEDDING_BASE_URL", "https://api.openai.com/v1")


class OpenLeaksConfig(Config):
    """Top-level pipeline configuration."""

    cache_dir: str = os.environ.get(
        "OPEN_LEAKS_CACHE_DIR",
        str(os.path.join(tempfile.gettempdir(), "open-leaks-cache")),
    )
    batch_size: int = int(os.environ.get("OPEN_LEAKS_BATCH_SIZE", "100"))

    # Download URLs (overridable for mirrors)
    icij_bulk_url: str = os.environ.get(
        "ICIJ_BULK_URL",
        "https://offshoreleaks-data.icij.org/offshoreleaks/csv/full-oldb.LATEST.zip",
    )
    cablegate_csv_url: str = os.environ.get(
        "CABLEGATE_CSV_URL",
        "https://archive.org/download/wikileaks-cables-csv/cables.csv",
    )
    epstein_api_url: str = os.environ.get(
        "EPSTEIN_API_URL",
        "https://www.epsteininvestigation.org/api/v1",
    )

    # Max records per source (0 = unlimited)
    max_cables: int = int(os.environ.get("MAX_CABLES", "0"))
    max_icij_entities: int = int(os.environ.get("MAX_ICIJ_ENTITIES", "0"))
    max_icij_relationships: int = int(os.environ.get("MAX_ICIJ_RELATIONSHIPS", "0"))
    max_epstein_docs: int = int(os.environ.get("MAX_EPSTEIN_DOCS", "0"))
