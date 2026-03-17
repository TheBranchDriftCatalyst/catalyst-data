from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class MathObjectKind(str, Enum):
    VARIABLE = "variable"
    CONSTANT = "constant"
    FUNCTION = "function"
    OPERATOR = "operator"
    SET = "set"
    RELATION = "relation"


class MathObject(BaseModel):
    symbol: str
    kind: MathObjectKind
    name: str | None = None
    latex: str | None = None
    domain: str | None = None


class MathPropositionKind(str, Enum):
    EQUATION = "equation"
    INEQUALITY = "inequality"
    DEFINITION = "definition"
    THEOREM = "theorem"
    AXIOM = "axiom"
    CONJECTURE = "conjecture"


class MathProposition(BaseModel):
    kind: MathPropositionKind
    statement: str
    latex: str | None = None
    objects: list[MathObject] = []
    dependencies: list[str] = []
