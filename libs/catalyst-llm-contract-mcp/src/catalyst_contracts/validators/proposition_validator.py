from __future__ import annotations

import re

from catalyst_contracts.models.evidence import IssueCode
from catalyst_contracts.models.validation import (
    ValidationErrorItem,
    ValidationResult,
    ValidationVerdict,
)

SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


def validate_propositions(
    propositions: list[dict],
    known_mention_ids: set[str],
    source_text: str,
) -> ValidationResult:
    errors: list[ValidationErrorItem] = []
    warnings: list[ValidationErrorItem] = []
    valid_items: list[int] = []
    invalid_items: list[int] = []

    for i, prop in enumerate(propositions):
        item_errors: list[ValidationErrorItem] = []
        path = f"propositions[{i}]"
        kind = prop.get("kind", "binary")

        if kind == "binary":
            # Check subject/object mention_id references
            subj_id = prop.get("subject_id")
            if subj_id is not None and subj_id not in known_mention_ids:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.subject_id",
                        code=IssueCode.INVALID_REFERENCE.value,
                        message=f"subject_id '{subj_id}' not in known mention IDs",
                    )
                )

            obj_id = prop.get("object_id")
            if obj_id is not None and obj_id not in known_mention_ids:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.object_id",
                        code=IssueCode.INVALID_REFERENCE.value,
                        message=f"object_id '{obj_id}' not in known mention IDs",
                    )
                )

            # Check alternative mention_id field names (LLMs may use these)
            subj_mid = prop.get("subject_mention_id")
            if subj_mid is not None and subj_mid not in known_mention_ids:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.subject_mention_id",
                        code=IssueCode.INVALID_REFERENCE.value,
                        message=f"subject_mention_id '{subj_mid}' not in known mention IDs",
                    )
                )

            obj_mid = prop.get("object_mention_id")
            if obj_mid is not None and obj_mid not in known_mention_ids:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.object_mention_id",
                        code=IssueCode.INVALID_REFERENCE.value,
                        message=f"object_mention_id '{obj_mid}' not in known mention IDs",
                    )
                )

        elif kind == "nary":
            # Check argument mention_id references
            for j, arg in enumerate(prop.get("arguments", [])):
                mid = arg.get("mention_id")
                if mid is not None and mid not in known_mention_ids:
                    item_errors.append(
                        ValidationErrorItem(
                            path=f"{path}.arguments[{j}].mention_id",
                            code=IssueCode.INVALID_REFERENCE.value,
                            message=f"mention_id '{mid}' not in known mention IDs",
                        )
                    )

        # Predicate format check
        predicate = prop.get("predicate", "")
        if predicate and not SNAKE_CASE_RE.match(predicate):
            warnings.append(
                ValidationErrorItem(
                    path=f"{path}.predicate",
                    code=IssueCode.INVALID_PREDICATE.value,
                    message=f"Predicate '{predicate}' is not snake_case",
                )
            )

        # Confidence bounds
        confidence = prop.get("confidence")
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
