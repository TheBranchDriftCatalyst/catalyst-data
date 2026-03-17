from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ValidationVerdict(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    ABSTAIN = "abstain"


class ValidationErrorItem(BaseModel):
    path: str
    code: str
    message: str
    context: dict[str, Any] | None = None


class ValidationResult(BaseModel):
    verdict: ValidationVerdict
    valid_count: int = 0
    invalid_count: int = 0
    errors: list[ValidationErrorItem] = []
    warnings: list[ValidationErrorItem] = []
    valid_items: list[int] = []
    invalid_items: list[int] = []
