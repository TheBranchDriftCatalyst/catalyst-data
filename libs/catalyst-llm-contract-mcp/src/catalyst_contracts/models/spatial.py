from __future__ import annotations

from pydantic import BaseModel, Field


class SpatialGroundingCandidate(BaseModel):
    """A candidate spatial grounding for a mention, with coordinates and geometry."""

    mention_id: str = Field(description="Composite ID of the mention being grounded (e.g., 'GPE:4:18')")
    lat: float = Field(ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    lon: float = Field(ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    h3_index: str | None = Field(default=None, description="H3 hexagonal index at the appropriate resolution")
    geometry_wkt: str | None = Field(default=None, description="Well-Known Text (WKT) representation of the geometry")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score for this grounding, between 0 and 1")
    max_supported_precision: int = Field(default=15, description="Maximum H3 resolution supported for this grounding")
