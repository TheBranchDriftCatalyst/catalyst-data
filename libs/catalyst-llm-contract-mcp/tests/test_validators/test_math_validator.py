from __future__ import annotations

from catalyst_contracts.models.validation import ValidationVerdict
from catalyst_contracts.validators.math_validator import validate_math


class TestValidateMath:
    def test_valid_proposition(self):
        props = [
            {
                "kind": "equation",
                "statement": "E = mc^2",
                "objects": [
                    {"symbol": "E", "kind": "variable"},
                    {"symbol": "m", "kind": "variable"},
                    {"symbol": "c", "kind": "constant"},
                ],
            }
        ]
        result = validate_math(props)
        assert result.verdict == ValidationVerdict.VALID

    def test_invalid_kind(self):
        props = [
            {
                "kind": "bogus_kind",
                "statement": "x = 1",
            }
        ]
        result = validate_math(props)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "INVALID_TYPE" for e in result.errors)

    def test_empty_statement(self):
        props = [
            {
                "kind": "equation",
                "statement": "   ",
            }
        ]
        result = validate_math(props)
        assert result.verdict == ValidationVerdict.INVALID

    def test_invalid_object_kind(self):
        props = [
            {
                "kind": "equation",
                "statement": "x = 1",
                "objects": [
                    {"symbol": "x", "kind": "not_a_kind"},
                ],
            }
        ]
        result = validate_math(props)
        assert result.verdict == ValidationVerdict.INVALID

    def test_empty_symbol(self):
        props = [
            {
                "kind": "equation",
                "statement": "x = 1",
                "objects": [
                    {"symbol": "", "kind": "variable"},
                ],
            }
        ]
        result = validate_math(props)
        assert result.verdict == ValidationVerdict.INVALID

    def test_empty_list(self):
        result = validate_math([])
        assert result.verdict == ValidationVerdict.VALID
