from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MathObjectKind(str, Enum):
    VARIABLE = "variable"
    CONSTANT = "constant"
    FUNCTION = "function"
    OPERATOR = "operator"
    SET = "set"
    RELATION = "relation"


class MathObject(BaseModel):
    """A mathematical object referenced in a proposition."""

    symbol: str = Field(description="The symbol representing this math object (e.g., 'x', 'f', '+')")
    kind: MathObjectKind = Field(description="Kind of math object: variable, constant, function, operator, set, or relation")
    name: str | None = Field(default=None, description="Human-readable name of the object (e.g., 'velocity')")
    latex: str | None = Field(default=None, description="LaTeX representation of the symbol")
    domain: str | None = Field(default=None, description="Mathematical domain (e.g., 'real numbers', 'integers')")


class MathPropositionKind(str, Enum):
    EQUATION = "equation"
    INEQUALITY = "inequality"
    DEFINITION = "definition"
    THEOREM = "theorem"
    AXIOM = "axiom"
    CONJECTURE = "conjecture"


class MathProposition(BaseModel):
    """A mathematical proposition extracted from text."""

    kind: MathPropositionKind = Field(description="Kind of proposition: equation, inequality, definition, theorem, axiom, or conjecture")
    statement: str = Field(description="The mathematical statement in natural language or symbolic form")
    latex: str | None = Field(default=None, description="LaTeX representation of the statement")
    objects: list[MathObject] = Field(default=[], description="Mathematical objects referenced in this proposition")
    dependencies: list[str] = Field(default=[], description="IDs of propositions this one depends on")
