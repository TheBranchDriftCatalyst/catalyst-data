"""Shared base types used across the KG pipeline."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from catalyst_contracts_core.enums import ExtractionMethod


class Provenance(BaseModel):
    """Tracks where an extraction came from."""

    source_document_id: str
    chunk_id: str
    span_start: int | None = None
    span_end: int | None = None
    extraction_method: ExtractionMethod = ExtractionMethod.LLM
    extraction_model: str = ""
    confidence: float = Field(default=1.0, ge=0, le=1)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    code_location: str = ""
