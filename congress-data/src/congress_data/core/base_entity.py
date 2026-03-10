"""Base entity with source tracking and factory pattern.

This abstraction will move to a shared Python utility library.
congress-data serves as the reference implementation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BaseEntity(BaseModel):
    """Pydantic base for domain entities extracted from external APIs."""

    id: str = Field(description="Unique entity identifier")
    source_url: str | None = Field(default=None, description="Source URL for this entity")

    @classmethod
    def from_api_response(cls, data: dict[str, Any], **kwargs: Any) -> BaseEntity:
        """Construct entity from raw API response. Override in subclasses."""
        raise NotImplementedError
