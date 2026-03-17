from __future__ import annotations

from catalyst_contracts.models.repair import RepairAction
from catalyst_contracts.models.validation import (
    ValidationErrorItem,
    ValidationResult,
    ValidationVerdict,
)
from catalyst_contracts.validators.repair_generator import generate_repair_plan


class TestGenerateRepairPlan:
    def test_empty_errors(self):
        result = ValidationResult(
            verdict=ValidationVerdict.VALID,
            valid_count=1,
        )
        plan = generate_repair_plan(result, {})
        assert plan.instructions == []
        assert plan.preserves_valid_fields is True

    def test_span_mismatch_repair(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="mentions[0].text",
                    code="SPAN_MISMATCH",
                    message="Text mismatch",
                )
            ],
            invalid_items=[0],
        )
        payload = {"mentions": [{"text": "wrong"}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        assert plan.instructions[0].action == RepairAction.REPLACE

    def test_confidence_repair_clamped(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="mentions[0].confidence",
                    code="CONFIDENCE_OUT_OF_RANGE",
                    message="Out of range",
                )
            ],
            invalid_items=[0],
        )
        payload = {"mentions": [{"confidence": 1.5}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        inst = plan.instructions[0]
        assert inst.action == RepairAction.COERCE
        assert inst.suggested_value == 1.0
        assert inst.auto_applicable is True

    def test_duplicate_span_repair(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="mentions[1].span",
                    code="DUPLICATE_SPAN",
                    message="Duplicate",
                )
            ],
            invalid_items=[1],
        )
        plan = generate_repair_plan(result, {"mentions": [{}, {}]})
        assert len(plan.instructions) == 1
        assert plan.instructions[0].action == RepairAction.DELETE
        assert plan.instructions[0].auto_applicable is True

    def test_invalid_type_repair(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="mentions[0].mention_type",
                    code="INVALID_TYPE",
                    message="Bad type",
                )
            ],
            invalid_items=[0],
        )
        payload = {"mentions": [{"mention_type": "BOGUS"}]}
        plan = generate_repair_plan(result, payload)
        assert plan.instructions[0].action == RepairAction.COERCE

    def test_invalid_reference_repair(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="propositions[0].subject_id",
                    code="INVALID_REFERENCE",
                    message="subject_id 'bad' not found",
                )
            ],
            invalid_items=[0],
        )
        payload = {"propositions": [{"subject_id": "bad"}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        inst = plan.instructions[0]
        assert inst.action == RepairAction.DELETE
        assert inst.auto_applicable is False
        assert inst.current_value == "bad"

    def test_coordinate_out_of_range_repair(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="candidates[0].lat",
                    code="COORDINATE_OUT_OF_RANGE",
                    message="Latitude 95 must be in [-90, 90]",
                )
            ],
            invalid_items=[0],
        )
        payload = {"candidates": [{"lat": 95.0}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        inst = plan.instructions[0]
        assert inst.action == RepairAction.COERCE
        assert inst.auto_applicable is False
        assert inst.current_value == 95.0

    def test_score_out_of_range_repair_with_numeric(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="candidates[0].score",
                    code="SCORE_OUT_OF_RANGE",
                    message="Score 1.5 out of range",
                )
            ],
            invalid_items=[0],
        )
        payload = {"candidates": [{"score": 1.5}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        inst = plan.instructions[0]
        assert inst.action == RepairAction.COERCE
        assert inst.suggested_value == 1.0
        assert inst.auto_applicable is True

    def test_score_out_of_range_repair_non_numeric(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="candidates[0].score",
                    code="SCORE_OUT_OF_RANGE",
                    message="Score 'abc' out of range",
                )
            ],
            invalid_items=[0],
        )
        payload = {"candidates": [{"score": "abc"}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        inst = plan.instructions[0]
        assert inst.action == RepairAction.COERCE
        assert inst.suggested_value is None
        assert inst.auto_applicable is False

    def test_unknown_entity_repair(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="mentions[0].entity_id",
                    code="UNKNOWN_ENTITY",
                    message="Entity not found",
                )
            ],
            invalid_items=[0],
        )
        payload = {"mentions": [{"entity_id": "ent_999"}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        inst = plan.instructions[0]
        assert inst.action == RepairAction.DELETE
        assert inst.auto_applicable is False
        assert inst.current_value == "ent_999"

    def test_unknown_code_fallback(self):
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="mentions[0].text",
                    code="TOTALLY_UNKNOWN_CODE",
                    message="Something weird",
                )
            ],
            invalid_items=[0],
        )
        payload = {"mentions": [{"text": "foo"}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        inst = plan.instructions[0]
        assert inst.action == RepairAction.REPLACE
        assert inst.auto_applicable is False
        assert inst.current_value == "foo"

    def test_resolve_path_non_dict_non_list(self):
        """Cover _resolve_path line 41: current is neither dict nor list."""
        result = ValidationResult(
            verdict=ValidationVerdict.INVALID,
            invalid_count=1,
            errors=[
                ValidationErrorItem(
                    path="mentions[0].text.nested",
                    code="SPAN_MISMATCH",
                    message="Deep path",
                )
            ],
            invalid_items=[0],
        )
        # text is a string, so resolving .nested on a string hits line 41
        payload = {"mentions": [{"text": "hello"}]}
        plan = generate_repair_plan(result, payload)
        assert len(plan.instructions) == 1
        assert plan.instructions[0].current_value is None
