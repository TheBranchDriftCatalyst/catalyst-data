from __future__ import annotations

import re

from catalyst_contracts.models.evidence import IssueCode
from catalyst_contracts.models.validation import (
    ValidationErrorItem,
    ValidationResult,
    ValidationVerdict,
)

WKT_PATTERN = re.compile(
    r"^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*\(.*\)$",
    re.IGNORECASE | re.DOTALL,
)


def _h3_resolution(h3_index: str) -> int | None:
    """Extract resolution from an H3 index string."""
    try:
        val = int(h3_index, 16)
        return (val >> 52) & 0xF
    except (ValueError, TypeError):
        return None


def validate_spatial(
    candidates: list[dict],
    source_text: str,
) -> ValidationResult:
    errors: list[ValidationErrorItem] = []
    warnings: list[ValidationErrorItem] = []
    valid_items: list[int] = []
    invalid_items: list[int] = []

    for i, cand in enumerate(candidates):
        item_errors: list[ValidationErrorItem] = []
        path = f"candidates[{i}]"

        # Lat/lon bounds
        lat = cand.get("lat")
        if lat is not None and (not isinstance(lat, (int, float)) or lat < -90.0 or lat > 90.0):
            item_errors.append(
                ValidationErrorItem(
                    path=f"{path}.lat",
                    code=IssueCode.COORDINATE_OUT_OF_RANGE.value,
                    message=f"Latitude {lat} must be in [-90, 90]",
                )
            )

        lon = cand.get("lon")
        if lon is not None and (not isinstance(lon, (int, float)) or lon < -180.0 or lon > 180.0):
            item_errors.append(
                ValidationErrorItem(
                    path=f"{path}.lon",
                    code=IssueCode.COORDINATE_OUT_OF_RANGE.value,
                    message=f"Longitude {lon} must be in [-180, 180]",
                )
            )

        # H3 resolution vs max_supported_precision
        h3_index = cand.get("h3_index")
        max_prec = cand.get("max_supported_precision", 15)
        if h3_index:
            res = _h3_resolution(h3_index)
            if res is not None and res > max_prec:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.h3_index",
                        code=IssueCode.PRECISION_EXCEEDED.value,
                        message=f"H3 resolution {res} exceeds "
                        f"max_supported_precision {max_prec}",
                    )
                )

        # WKT geometry
        geometry_wkt = cand.get("geometry_wkt")
        if geometry_wkt and not WKT_PATTERN.match(geometry_wkt.strip()):
            item_errors.append(
                ValidationErrorItem(
                    path=f"{path}.geometry_wkt",
                    code=IssueCode.INVALID_GEOMETRY.value,
                    message=f"geometry_wkt is not valid WKT: '{geometry_wkt[:60]}'",
                )
            )

        # Confidence bounds
        confidence = cand.get("confidence")
        if confidence is not None and (
            not isinstance(confidence, (int, float))
            or confidence < 0.0
            or confidence > 1.0
        ):
            item_errors.append(
                ValidationErrorItem(
                    path=f"{path}.confidence",
                    code=IssueCode.CONFIDENCE_OUT_OF_RANGE.value,
                    message=f"Confidence {confidence} must be in [0.0, 1.0]",
                )
            )

        if item_errors:
            errors.extend(item_errors)
            invalid_items.append(i)
        else:
            valid_items.append(i)

    valid_count = len(valid_items)
    invalid_count = len(invalid_items)

    if invalid_count == 0:
        verdict = ValidationVerdict.VALID
    elif valid_count == 0:
        verdict = ValidationVerdict.INVALID
    else:
        verdict = ValidationVerdict.AMBIGUOUS

    return ValidationResult(
        verdict=verdict,
        valid_count=valid_count,
        invalid_count=invalid_count,
        errors=errors,
        warnings=warnings,
        valid_items=valid_items,
        invalid_items=invalid_items,
    )
