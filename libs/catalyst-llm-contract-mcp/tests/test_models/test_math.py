from __future__ import annotations

from catalyst_contracts.models.math import (
    MathObject,
    MathObjectKind,
    MathProposition,
    MathPropositionKind,
)


class TestMathObject:
    def test_valid_object(self):
        obj = MathObject(
            symbol="x",
            kind=MathObjectKind.VARIABLE,
            name="position",
            latex="x",
            domain="real",
        )
        assert obj.symbol == "x"
        assert obj.kind == MathObjectKind.VARIABLE

    def test_minimal_object(self):
        obj = MathObject(symbol="f", kind=MathObjectKind.FUNCTION)
        assert obj.name is None
        assert obj.latex is None
        assert obj.domain is None


class TestMathProposition:
    def test_valid_proposition(self):
        p = MathProposition(
            kind=MathPropositionKind.EQUATION,
            statement="E = mc^2",
            latex=r"E = mc^2",
            objects=[
                MathObject(symbol="E", kind=MathObjectKind.VARIABLE),
                MathObject(symbol="m", kind=MathObjectKind.VARIABLE),
                MathObject(symbol="c", kind=MathObjectKind.CONSTANT),
            ],
        )
        assert len(p.objects) == 3
        assert p.dependencies == []

    def test_all_proposition_kinds(self):
        for kind in MathPropositionKind:
            p = MathProposition(kind=kind, statement="x = 1")
            assert p.kind == kind
