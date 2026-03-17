from __future__ import annotations

from catalyst_contracts.models.evidence import IssueCode
from catalyst_contracts.models.math import MathObjectKind, MathPropositionKind
from catalyst_contracts.models.validation import (
    ValidationErrorItem,
    ValidationResult,
    ValidationVerdict,
)


def validate_math(
    propositions: list[dict],
) -> ValidationResult:
    errors: list[ValidationErrorItem] = []
    warnings: list[ValidationErrorItem] = []
    valid_items: list[int] = []
    invalid_items: list[int] = []

    valid_kinds = {k.value for k in MathPropositionKind}
    valid_obj_kinds = {k.value for k in MathObjectKind}

    for i, prop in enumerate(propositions):
        item_errors: list[ValidationErrorItem] = []
        path = f"math_propositions[{i}]"

        # Validate proposition kind
        kind = prop.get("kind", "")
        if kind not in valid_kinds:
            item_errors.append(
                ValidationErrorItem(
                    path=f"{path}.kind",
                    code=IssueCode.INVALID_TYPE.value,
                    message=f"Invalid math proposition kind '{kind}'. "
                    f"Must be one of: {sorted(valid_kinds)}",
                )
            )

        # Validate statement is non-empty
        statement = prop.get("statement", "")
        if not statement.strip():
            item_errors.append(
                ValidationErrorItem(
                    path=f"{path}.statement",
                    code=IssueCode.MISSING_EVIDENCE.value,
                    message="Math proposition statement must not be empty",
                )
            )

        # Validate objects
        for j, obj in enumerate(prop.get("objects", [])):
            obj_kind = obj.get("kind", "")
            if obj_kind not in valid_obj_kinds:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.objects[{j}].kind",
                        code=IssueCode.INVALID_TYPE.value,
                        message=f"Invalid math object kind '{obj_kind}'. "
                        f"Must be one of: {sorted(valid_obj_kinds)}",
                    )
                )

            symbol = obj.get("symbol", "")
            if not symbol.strip():
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{path}.objects[{j}].symbol",
                        code=IssueCode.MISSING_EVIDENCE.value,
                        message="Math object symbol must not be empty",
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
