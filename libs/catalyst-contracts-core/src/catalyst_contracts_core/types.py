"""Shared base types used across the KG pipeline."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from catalyst_contracts_core.enums import ExtractionMethod


class Provenance(BaseModel):
    """Tracks where an extraction came from, including source document, method, and confidence."""

    source_document_id: str = Field(description="ID of the source document this extraction came from")
    chunk_id: str = Field(description="ID of the chunk within the source document")
    span_start: int | None = Field(default=None, description="Character offset where the extracted span starts")
    span_end: int | None = Field(default=None, description="Character offset where the extracted span ends")
    extraction_method: ExtractionMethod = Field(default=ExtractionMethod.LLM, description="Method used for extraction: llm, spacy, regex, manual, or structured")
    extraction_model: str = Field(default="", description="Name/ID of the model used for extraction")
    confidence: float = Field(default=1.0, ge=0, le=1, description="Confidence score for this extraction, between 0 and 1")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 timestamp of when this extraction was performed",
    )
    code_location: str = Field(default="", description="Dagster code location that produced this extraction")
