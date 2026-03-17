from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class RepairAction(str, Enum):
    REPLACE = "replace"
    DELETE = "delete"
    INSERT = "insert"
    COERCE = "coerce"


class RepairInstruction(BaseModel):
    path: str
    action: RepairAction
    current_value: Any = None
    suggested_value: Any = None
    reason: str
    auto_applicable: bool = False


class RepairPlan(BaseModel):
    instructions: list[RepairInstruction] = []
    preserves_valid_fields: bool = True
