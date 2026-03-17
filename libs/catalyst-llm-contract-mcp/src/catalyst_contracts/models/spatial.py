from __future__ import annotations

from pydantic import BaseModel, Field


class SpatialGroundingCandidate(BaseModel):
    mention_id: str
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    h3_index: str | None = None
    geometry_wkt: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    max_supported_precision: int = 15
