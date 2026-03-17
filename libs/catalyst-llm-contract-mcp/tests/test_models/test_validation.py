from __future__ import annotations

from catalyst_contracts.models.validation import (
    ValidationErrorItem,
    ValidationResult,
    ValidationVerdict,
)


class TestValidationVerdict:
    def test_all_verdicts(self):
        assert ValidationVerdict.VALID == "valid"
        assert ValidationVerdict.INVALID == "invalid"
        assert ValidationVerdict.AMBIGUOUS == "ambiguous"
        assert ValidationVerdict.ABSTAIN == "abstain"


class TestValidationResult:
    def test_valid_result(self):
        result = ValidationResult(
            verdict=ValidationVerdict.VALID,
            valid_count=3,
            invalid_count=0,
            valid_items=[0, 1, 2],
        )
        assert result.verdict == ValidationVerdict.VALID
        assert result.valid_count == 3
        assert result.errors == []

    def test_invalid_result_with_errors(self):
        err = ValidationErrorItem(
            path="mentions[0].text",
            code="SPAN_MISMATCH",
            message="text does not match",
        )
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            valid_count=0,
            invalid_count=1,
            errors=[err],
            invalid_items=[0],
        )
        assert len(result.errors) == 1
        assert result.errors[0].code == "SPAN_MISMATCH"

    def test_error_item_with_context(self):
        err = ValidationErrorItem(
            path="x",
            code="ERR",
            message="msg",
            context={"expected": "foo", "got": "bar"},
        )
        assert err.context["expected"] == "foo"
