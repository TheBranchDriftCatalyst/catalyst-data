from __future__ import annotations

from catalyst_contracts.models.repair import (
    RepairAction,
    RepairInstruction,
    RepairPlan,
)


class TestRepairInstruction:
    def test_replace_instruction(self):
        inst = RepairInstruction(
            path="mentions[0].text",
            action=RepairAction.REPLACE,
            current_value="wrong",
            suggested_value="correct",
            reason="Text mismatch",
            auto_applicable=True,
        )
        assert inst.action == RepairAction.REPLACE
        assert inst.auto_applicable is True

    def test_delete_instruction(self):
        inst = RepairInstruction(
            path="mentions[1]",
            action=RepairAction.DELETE,
            reason="Duplicate span",
        )
        assert inst.current_value is None
        assert inst.suggested_value is None
        assert inst.auto_applicable is False


class TestRepairPlan:
    def test_empty_plan(self):
        plan = RepairPlan()
        assert plan.instructions == []
        assert plan.preserves_valid_fields is True

    def test_plan_with_instructions(self):
        plan = RepairPlan(
            instructions=[
                RepairInstruction(
                    path="x",
                    action=RepairAction.COERCE,
                    reason="out of range",
                ),
            ],
            preserves_valid_fields=True,
        )
        assert len(plan.instructions) == 1
