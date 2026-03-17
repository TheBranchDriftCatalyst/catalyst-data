from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ValidationVerdict(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    ABSTAIN = "abstain"


class ValidationErrorItem(BaseModel):
    """A single validation error or warning with location and context."""

    path: str = Field(description="JSON path to the problematic field (e.g., 'mentions[0].text')")
    code: str = Field(description="Machine-readable error code (e.g., 'SPAN_MISMATCH')")
    message: str = Field(description="Human-readable description of the validation error")
    context: dict[str, Any] | None = Field(default=None, description="Additional context about the error")


class ValidationResult(BaseModel):
    """Result of validating a set of extraction candidates."""

    verdict: ValidationVerdict = Field(description="Overall verdict: valid, invalid, ambiguous, or abstain")
    valid_count: int = Field(default=0, description="Number of items that passed validation")
    invalid_count: int = Field(default=0, description="Number of items that failed validation")
    errors: list[ValidationErrorItem] = Field(default=[], description="List of validation errors found")
    warnings: list[ValidationErrorItem] = Field(default=[], description="List of validation warnings found")
    valid_items: list[int] = Field(default=[], description="Indices of valid items in the input list")
    invalid_items: list[int] = Field(default=[], description="Indices of invalid items in the input list")
