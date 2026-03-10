"""Unified datalake Document model.

All domains produce Document objects as their canonical output.
This model is the interchange format between pipeline stages.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Document(BaseModel):
    """Unified document model for the datalake."""

    id: str = Field(description="Unique document identifier")
    title: str = Field(description="Document title")
    content: str = Field(description="Full text content")
    source: str = Field(description="Data source (e.g. 'wikileaks')")
    source_url: str | None = Field(default=None, description="Original URL")
    document_type: str = Field(description="Document type (e.g. 'cable', 'court_document')")
    domain: str = Field(description="Domain namespace (e.g. 'open_leaks')")
    entity_type: str = Field(description="Source entity type (e.g. 'Cable', 'CourtDocument')")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")
    sections: dict[str, str] = Field(default_factory=dict, description="Named content sections")
    content_hash: str = Field(default="", description="SHA-256 of content for dedup")

    @model_validator(mode="after")
    def _compute_content_hash(self) -> Document:
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()
        return self
