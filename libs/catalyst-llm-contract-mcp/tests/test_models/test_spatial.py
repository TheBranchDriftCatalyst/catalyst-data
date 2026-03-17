from __future__ import annotations

import pytest
from pydantic import ValidationError

from catalyst_contracts.models.spatial import SpatialGroundingCandidate


class TestSpatialGroundingCandidate:
    def test_valid_candidate(self):
        c = SpatialGroundingCandidate(
            mention_id="m1",
            lat=40.7128,
            lon=-74.0060,
            confidence=0.95,
        )
        assert c.lat == 40.7128
        assert c.h3_index is None
        assert c.geometry_wkt is None
        assert c.max_supported_precision == 15

    def test_lat_bounds(self):
        with pytest.raises(ValidationError):
            SpatialGroundingCandidate(
                mention_id="m1", lat=91.0, lon=0.0, confidence=0.5
            )
        with pytest.raises(ValidationError):
            SpatialGroundingCandidate(
                mention_id="m1", lat=-91.0, lon=0.0, confidence=0.5
            )

    def test_lon_bounds(self):
        with pytest.raises(ValidationError):
            SpatialGroundingCandidate(
                mention_id="m1", lat=0.0, lon=181.0, confidence=0.5
            )
        with pytest.raises(ValidationError):
            SpatialGroundingCandidate(
                mention_id="m1", lat=0.0, lon=-181.0, confidence=0.5
            )

    def test_with_optional_fields(self):
        c = SpatialGroundingCandidate(
            mention_id="m1",
            lat=51.5074,
            lon=-0.1278,
            h3_index="891f1d48177ffff",
            geometry_wkt="POINT(-0.1278 51.5074)",
            confidence=0.88,
            max_supported_precision=9,
        )
        assert c.h3_index == "891f1d48177ffff"
        assert c.geometry_wkt == "POINT(-0.1278 51.5074)"
