from __future__ import annotations

from catalyst_contracts.models.evidence import IssueCode
from catalyst_contracts.models.validation import (
    ValidationErrorItem,
    ValidationResult,
    ValidationVerdict,
)


def validate_concordance(
    candidate_sets: list[dict],
    known_entity_ids: set[str],
) -> ValidationResult:
    errors: list[ValidationErrorItem] = []
    warnings: list[ValidationErrorItem] = []
    valid_items: list[int] = []
    invalid_items: list[int] = []

    score_fields = ("exact", "substring", "jaccard", "cosine", "combined")

    for i, cs in enumerate(candidate_sets):
        item_errors: list[ValidationErrorItem] = []
        path = f"candidate_sets[{i}]"

        for j, cand in enumerate(cs.get("candidates", [])):
            cand_path = f"{path}.candidates[{j}]"

            # Entity ID must be in known set
            entity_id = cand.get("entity_id", "")
            if entity_id and entity_id not in known_entity_ids:
                item_errors.append(
                    ValidationErrorItem(
                        path=f"{cand_path}.entity_id",
                        code=IssueCode.UNKNOWN_ENTITY.value,
                        message=f"entity_id '{entity_id}' not in known entity IDs",
                    )
                )

            # Score bounds check
            for field in score_fields:
                score = cand.get(field)
                if score is not None and (
                    not isinstance(score, (int, float))
                    or score < 0.0
                    or score > 1.0
                ):
                    item_errors.append(
                        ValidationErrorItem(
                            path=f"{cand_path}.{field}",
                            code=IssueCode.SCORE_OUT_OF_RANGE.value,
                            message=f"Score '{field}' = {score} must be in [0.0, 1.0]",
                        )
                    )

            # Combined score consistency: should be >= min and <= max of components
            component_scores = []
            for field in ("exact", "substring", "jaccard", "cosine"):
                s = cand.get(field)
                if isinstance(s, (int, float)):
                    component_scores.append(s)
            combined = cand.get("combined")
            if (
                component_scores
                and isinstance(combined, (int, float))
                and 0.0 <= combined <= 1.0
            ):
                if combined > max(component_scores) + 0.01:
                    warnings.append(
                        ValidationErrorItem(
                            path=f"{cand_path}.combined",
                            code=IssueCode.INCONSISTENT_SCORES.value,
                            message=f"Combined score {combined} exceeds max "
                            f"component score {max(component_scores):.4f}",
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
