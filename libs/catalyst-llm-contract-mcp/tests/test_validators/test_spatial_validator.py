from __future__ import annotations

from catalyst_contracts.models.validation import ValidationVerdict
from catalyst_contracts.validators.spatial_validator import validate_spatial


class TestValidateSpatial:
    def test_valid_candidates(self, source_text):
        candidates = [
            {
                "mention_id": "m1",
                "lat": 40.7128,
                "lon": -74.0060,
                "confidence": 0.95,
            }
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_lat_out_of_range(self, source_text):
        candidates = [
            {
                "mention_id": "m1",
                "lat": 95.0,
                "lon": 0.0,
                "confidence": 0.5,
            }
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "COORDINATE_OUT_OF_RANGE" for e in result.errors)

    def test_lon_out_of_range(self, source_text):
        candidates = [
            {
                "mention_id": "m1",
                "lat": 0.0,
                "lon": -200.0,
                "confidence": 0.5,
            }
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.INVALID

    def test_invalid_geometry_wkt(self, source_text):
        candidates = [
            {
                "mention_id": "m1",
                "lat": 0.0,
                "lon": 0.0,
                "geometry_wkt": "NOT_WKT garbage",
                "confidence": 0.5,
            }
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "INVALID_GEOMETRY" for e in result.errors)

    def test_valid_wkt(self, source_text):
        candidates = [
            {
                "mention_id": "m1",
                "lat": 51.5,
                "lon": -0.1,
                "geometry_wkt": "POINT(-0.1 51.5)",
                "confidence": 0.9,
            }
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_confidence_out_of_range(self, source_text):
        candidates = [
            {
                "mention_id": "m1",
                "lat": 0.0,
                "lon": 0.0,
                "confidence": 1.5,
            }
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.INVALID

    def test_empty_candidates(self, source_text):
        result = validate_spatial([], source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_lat_boundary_positive_valid(self, source_text):
        candidates = [
            {"mention_id": "m1", "lat": 90.0, "lon": 0.0, "confidence": 0.5}
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_lat_boundary_negative_valid(self, source_text):
        candidates = [
            {"mention_id": "m1", "lat": -90.0, "lon": 0.0, "confidence": 0.5}
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_lon_boundary_positive_valid(self, source_text):
        candidates = [
            {"mention_id": "m1", "lat": 0.0, "lon": 180.0, "confidence": 0.5}
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_lon_boundary_negative_valid(self, source_text):
        candidates = [
            {"mention_id": "m1", "lat": 0.0, "lon": -180.0, "confidence": 0.5}
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_h3_precision_exceeded(self, source_text):
        """H3 index with resolution > max_supported_precision triggers error."""
        # Craft an H3-like hex string where resolution bits (bits 52-55) encode
        # resolution 15.  We set max_supported_precision=7 so 15 > 7 triggers the
        # PRECISION_EXCEEDED branch.
        # 0x0F << 52 sets resolution nibble to 15.
        h3_val = 0x0F << 52
        h3_hex = format(h3_val, "x")
        candidates = [
            {
                "mention_id": "m1",
                "lat": 0.0,
                "lon": 0.0,
                "h3_index": h3_hex,
                "max_supported_precision": 7,
                "confidence": 0.5,
            }
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "PRECISION_EXCEEDED" for e in result.errors)

    def test_h3_invalid_hex_returns_none(self, source_text):
        """Non-hex h3_index should not crash; _h3_resolution returns None."""
        candidates = [
            {
                "mention_id": "m1",
                "lat": 0.0,
                "lon": 0.0,
                "h3_index": "not_a_hex_value",
                "max_supported_precision": 7,
                "confidence": 0.5,
            }
        ]
        result = validate_spatial(candidates, source_text)
        # No precision error because _h3_resolution returns None
        assert not any(e.code == "PRECISION_EXCEEDED" for e in result.errors)

    def test_h3_none_index_no_error(self, source_text):
        """When h3_index is None/empty, no H3 check is performed."""
        candidates = [
            {
                "mention_id": "m1",
                "lat": 0.0,
                "lon": 0.0,
                "h3_index": "",
                "confidence": 0.5,
            }
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_ambiguous_verdict_mixed_candidates(self, source_text):
        """Mix of valid and invalid candidates yields AMBIGUOUS."""
        candidates = [
            {
                "mention_id": "m1",
                "lat": 0.0,
                "lon": 0.0,
                "confidence": 0.5,
            },
            {
                "mention_id": "m2",
                "lat": 999.0,  # invalid
                "lon": 0.0,
                "confidence": 0.5,
            },
        ]
        result = validate_spatial(candidates, source_text)
        assert result.verdict == ValidationVerdict.AMBIGUOUS
        assert result.valid_count == 1
        assert result.invalid_count == 1
