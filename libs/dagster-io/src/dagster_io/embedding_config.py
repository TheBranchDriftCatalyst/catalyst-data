"""Embedding configuration model with deterministic config key generation.

The config key is a short, human-readable slug that identifies a particular
chunking/embedding configuration.  It is used as a Hive-style partition
segment (``config=<key>``) so that different configurations produce separate
S3 assets that can coexist without overwriting each other.
"""

from __future__ import annotations

import hashlib
import os
import re

from dagster import ConfigurableResource
from pydantic import BaseModel

# Short model aliases for config key slugs
_MODEL_ALIASES: dict[str, str] = {
    "text-embedding-3-small": "te3s",
    "text-embedding-3-large": "te3l",
    "text-embedding-ada-002": "ada2",
}


def _model_slug(model: str) -> str:
    """Return a short alias for *model*, or a sanitized abbreviation."""
    if model in _MODEL_ALIASES:
        return _MODEL_ALIASES[model]
    # Strip common prefixes and keep alphanumeric chars
    slug = re.sub(r"[^a-z0-9]", "", model.lower())
    return slug[:8] if slug else "unk"


class EmbeddingConfig(BaseModel):
    """Describes a full chunking + embedding configuration."""

    chunk_size: int = 1000
    chunk_overlap: int = 200
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int | None = None
    prepend_title: bool = True
    strategy: str = "recursive"

    @property
    def config_key(self) -> str:
        """Deterministic human-readable slug, e.g. ``cs1000_co200_te3s``."""
        parts = [
            f"cs{self.chunk_size}",
            f"co{self.chunk_overlap}",
            _model_slug(self.embedding_model),
        ]
        if self.embedding_dimensions is not None:
            parts.append(f"d{self.embedding_dimensions}")
        return "_".join(parts)

    @property
    def config_hash(self) -> str:
        """Short SHA-256 for verification (first 8 hex chars)."""
        canonical = (
            f"{self.chunk_size}:{self.chunk_overlap}:{self.embedding_model}"
            f":{self.embedding_dimensions}:{self.prepend_title}:{self.strategy}"
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:8]

    def to_metadata(self) -> dict:
        """Return a dict suitable for sidecar / manifest inclusion."""
        return {
            "config_key": self.config_key,
            "config_hash": self.config_hash,
            **self.model_dump(),
        }


class EmbeddingConfigResource(ConfigurableResource):
    """Dagster resource that provides a typed EmbeddingConfig to assets.

    Assets inject the config key into output metadata so the IO manager
    can route data to config-specific S3 paths.
    """

    chunk_size: int = int(os.environ.get("CHUNK_SIZE", "1000"))
    chunk_overlap: int = int(os.environ.get("CHUNK_OVERLAP", "200"))
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dimensions: int | None = None
    prepend_title: bool = True

    def get_config(self) -> EmbeddingConfig:
        return EmbeddingConfig(**self.model_dump())

    @property
    def config_key(self) -> str:
        return self.get_config().config_key
