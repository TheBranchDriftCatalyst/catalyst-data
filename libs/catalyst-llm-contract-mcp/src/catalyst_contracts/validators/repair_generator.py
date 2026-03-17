from __future__ import annotations

from typing import Any

from catalyst_contracts.models.repair import RepairAction, RepairInstruction, RepairPlan
from catalyst_contracts.models.validation import ValidationResult


def generate_repair_plan(
    validation_result: ValidationResult,
    original_payload: dict[str, Any],
) -> RepairPlan:
    instructions: list[RepairInstruction] = []

    for err in validation_result.errors:
        instruction = _error_to_instruction(err, original_payload)
        if instruction:
            instructions.append(instruction)

    return RepairPlan(
        instructions=instructions,
        preserves_valid_fields=True,
    )


def _resolve_path(payload: dict[str, Any], path: str) -> Any:
    """Attempt to resolve a dotted/bracketed path in the payload."""
    import re

    parts = re.split(r"[\.\[\]]", path)
    parts = [p for p in parts if p]

    current: Any = payload
    for part in parts:
        try:
            if isinstance(current, dict):
                current = current[part]
            elif isinstance(current, list):
                current = current[int(part)]
            else:
                return None
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return current


def _error_to_instruction(
    error: Any,
    payload: dict[str, Any],
) -> RepairInstruction | None:
    code = error.code
    path = error.path

    current_value = _resolve_path(payload, path)

    if code == "SPAN_MISMATCH":
        return RepairInstruction(
            path=path,
            action=RepairAction.REPLACE,
            current_value=current_value,
            suggested_value=None,
            reason=error.message,
            auto_applicable=False,
        )
    elif code == "INVALID_TYPE":
        return RepairInstruction(
            path=path,
            action=RepairAction.COERCE,
            current_value=current_value,
            suggested_value=None,
            reason=error.message,
            auto_applicable=False,
        )
    elif code == "CONFIDENCE_OUT_OF_RANGE":
        suggested = None
        if isinstance(current_value, (int, float)):
            suggested = max(0.0, min(1.0, float(current_value)))
        return RepairInstruction(
            path=path,
            action=RepairAction.COERCE,
            current_value=current_value,
            suggested_value=suggested,
            reason=error.message,
            auto_applicable=suggested is not None,
        )
    elif code == "DUPLICATE_SPAN":
        return RepairInstruction(
            path=path,
            action=RepairAction.DELETE,
            current_value=current_value,
            suggested_value=None,
            reason=error.message,
            auto_applicable=True,
        )
    elif code == "INVALID_REFERENCE":
        return RepairInstruction(
            path=path,
            action=RepairAction.DELETE,
            current_value=current_value,
            suggested_value=None,
            reason=error.message,
            auto_applicable=False,
        )
    elif code == "COORDINATE_OUT_OF_RANGE":
        return RepairInstruction(
            path=path,
            action=RepairAction.COERCE,
            current_value=current_value,
            suggested_value=None,
            reason=error.message,
            auto_applicable=False,
        )
    elif code == "SCORE_OUT_OF_RANGE":
        suggested = None
        if isinstance(current_value, (int, float)):
            suggested = max(0.0, min(1.0, float(current_value)))
        return RepairInstruction(
            path=path,
            action=RepairAction.COERCE,
            current_value=current_value,
            suggested_value=suggested,
            reason=error.message,
            auto_applicable=suggested is not None,
        )
    elif code == "UNKNOWN_ENTITY":
        return RepairInstruction(
            path=path,
            action=RepairAction.DELETE,
            current_value=current_value,
            suggested_value=None,
            reason=error.message,
            auto_applicable=False,
        )

    return RepairInstruction(
        path=path,
        action=RepairAction.REPLACE,
        current_value=current_value,
        suggested_value=None,
        reason=error.message,
        auto_applicable=False,
    )
