from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RepairAction(str, Enum):
    REPLACE = "replace"
    DELETE = "delete"
    INSERT = "insert"
    COERCE = "coerce"


class RepairInstruction(BaseModel):
    """A single repair instruction for fixing a validation error."""

    path: str = Field(description="JSON path to the field to repair (e.g., 'mentions[0].span_start')")
    action: RepairAction = Field(description="Repair action: replace, delete, insert, or coerce")
    current_value: Any = Field(default=None, description="The current value of the field")
    suggested_value: Any = Field(default=None, description="The suggested replacement value")
    reason: str = Field(description="Human-readable explanation of why this repair is needed")
    auto_applicable: bool = Field(default=False, description="Whether this repair can be applied automatically without LLM re-generation")


class RepairPlan(BaseModel):
    """A plan for repairing validation errors in extraction output."""

    instructions: list[RepairInstruction] = Field(default=[], description="Ordered list of repair instructions")
    preserves_valid_fields: bool = Field(default=True, description="Whether applying this plan preserves fields that already passed validation")
