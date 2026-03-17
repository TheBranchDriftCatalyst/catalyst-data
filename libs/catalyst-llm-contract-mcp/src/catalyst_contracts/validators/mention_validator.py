from __future__ import annotations

from catalyst_contracts_core.enums import MentionType

from catalyst_contracts.models.evidence import IssueCode
from catalyst_contracts.models.validation import (
    ValidationErrorItem,
    ValidationResult,
    ValidationVerdict,
)

# Alias map for common LLM entity type variations
ENTITY_TYPE_ALIASES = {
    "ORGANIZATION": "ORG",
    "LOCATION": "LOC",
    "GEOPOLITICAL_ENTITY": "GPE",
    "GEO_POLITICAL_ENTITY": "GPE",
    "GEOGRAPHIC": "LOC",
    "COMPANY": "ORG",
    "COUNTRY": "GPE",
    "CITY": "GPE",
    "STATE": "GPE",
}


def validate_mentions(
    mentions: list[dict],
    source_text: str,
    document_id: str,
) -> ValidationResult:
    errors: list[ValidationErrorItem] = []
    warnings: list[ValidationErrorItem] = []
    valid_items: list[int] = []
    invalid_items: list[int] = []
    seen_spans: set[tuple[int, int]] = set()

    valid_types = {t.value for t in MentionType}

    if len(mentions) == 0:
        return ValidationResult(
            verdict=ValidationVerdict.INVALID,
            valid_count=0,
            invalid_count=0,
            errors=[
                ValidationErrorItem(
                    path="mentions",
                    code="EMPTY_EXTRACTION",
                    message="No mention candidates provided — extraction may have failed",
                )
            ],
        )

    for i, m in enumerate(mentions):
        item_errors: list[ValidationErrorItem] = []
        path = f"mentions[{i}]"

        # Required field presence check
        required_fields = ["text", "mention_type"]
        missing = [f for f in required_fields if not m.get(f)]
        if missing:
            item_errors.append(
                ValidationErrorItem(
                    path=path,
                    code=IssueCode.MISSING_REQUIRED_FIELD.value,
                    message=f"Missing required fields: {', '.join(missing)}",
                )
            )

        span_start = m.get("span_start")
        span_end = m.get("span_end")
        text = m.get("text", "")

        # Span alignment check
        if (
            isinstance(span_start, int)
            and isinstance(span_end, int)
            and span_start >= 0
            and span_end <= len(source_text)
            and span_end > span_start
        ):
            expected = source_text[span_start:span_end]
            if text != expected:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.text",
                        code=IssueCode.SPAN_MISMATCH.value,
                        message=f"Span text '{text}' does not match source "
                        f"text '{expected}' at [{span_start}:{span_end}]",
                    )
                )
        elif span_start is not None and span_end is not None:
            item_errors.append(
                ValidationErrorItem(
                    path=f"{path}.span",
                    code=IssueCode.SPAN_MISMATCH.value,
                    message=f"Invalid span range [{span_start}:{span_end}] "
                    f"for source text of length {len(source_text)}",
                )
            )

        # Valid mention type (with alias normalization)
        mention_type = m.get("mention_type", "")
        mention_type = ENTITY_TYPE_ALIASES.get(mention_type.upper(), mention_type.upper()) if mention_type else ""
        if mention_type not in valid_types:
            item_errors.append(
                ValidationErrorItem(
                    path=f"{path}.mention_type",
                    code=IssueCode.INVALID_TYPE.value,
                    message=f"Invalid mention type '{mention_type}'. "
                    f"Must be one of: {sorted(valid_types)}",
                )
            )

        # Confidence bounds
        confidence = m.get("confidence")
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

        # Duplicate span check
        if isinstance(span_start, int) and isinstance(span_end, int):
            span_key = (span_start, span_end)
            if span_key in seen_spans:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.span",
                        code=IssueCode.DUPLICATE_SPAN.value,
                        message=f"Duplicate span [{span_start}:{span_end}]",
                    )
                )
            seen_spans.add(span_key)

        # Evidence span consistency
        for j, ev in enumerate(m.get("evidence", [])):
            ev_text = ev.get("text", "")
            ev_start = ev.get("span_start")
            ev_end = ev.get("span_end")
            if (
                isinstance(ev_start, int)
                and isinstance(ev_end, int)
                and ev_end > ev_start
            ):
                if len(ev_text) != ev_end - ev_start:
                    warnings.append(
                        ValidationErrorItem(
                            path=f"{path}.evidence[{j}].text",
                            code=IssueCode.SPAN_MISMATCH.value,
                            message=f"Evidence text length ({len(ev_text)}) "
                            f"does not match span length ({ev_end - ev_start})",
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
