"""Behavioral tests for ExGraphState and StageResult."""

from __future__ import annotations

from catalyst_exgraph.protocol import StageResult
from catalyst_exgraph.state import ExGraphState, ExGraphStatus, StageStateDict

# ── ExGraphState construction ───────────────────────────────────────────────


def test_exgraph_state_stages_holds_multiple_entries():
    state: ExGraphState = {
        "stages": {
            "ner": StageStateDict(candidates=[{"text": "Alice"}], accepted=[]),
            "spo": StageStateDict(candidates=[], accepted=[{"subj": "Alice"}]),
        }
    }
    assert "ner" in state["stages"]
    assert "spo" in state["stages"]
    assert state["stages"]["ner"]["candidates"] == [{"text": "Alice"}]
    assert state["stages"]["spo"]["accepted"] == [{"subj": "Alice"}]


# ── ExGraphStatus enum ─────────────────────────────────────────────────────


def test_exgraph_status_contains_expected_members():
    expected = {"pending", "extracting", "validating", "repairing", "persisting", "completed", "failed", "skipped"}
    actual = {m.value for m in ExGraphStatus}
    assert actual == expected


# ── StageResult round-trip ──────────────────────────────────────────────────


def test_stage_result_roundtrip_preserves_data(populated_stage_result: StageResult):
    d = populated_stage_result.to_dict()
    restored = StageResult.from_dict(d)

    assert restored.candidates == populated_stage_result.candidates
    assert restored.accepted == populated_stage_result.accepted
    assert restored.validation == populated_stage_result.validation
    assert restored.retry_count == populated_stage_result.retry_count
    assert restored.audit_events == populated_stage_result.audit_events
    assert restored.status == populated_stage_result.status
    assert restored.error == populated_stage_result.error


def test_stage_result_roundtrip_with_empty_fields():
    original = StageResult()
    d = original.to_dict()
    restored = StageResult.from_dict(d)

    assert restored.candidates == []
    assert restored.accepted == []
    assert restored.validation == {}
    assert restored.retry_count == 0
    assert restored.status == "pending"
    assert restored.error == ""


def test_stage_result_from_dict_missing_keys_uses_defaults():
    """from_dict with a partial (or empty) dict should fill in sensible defaults."""
    restored = StageResult.from_dict({})

    assert restored.candidates == []
    assert restored.accepted == []
    assert restored.validation == {}
    assert restored.retry_count == 0
    assert restored.audit_events == []
    assert restored.status == "pending"
    assert restored.error == ""


def test_stage_result_from_dict_with_partial_keys():
    restored = StageResult.from_dict({"status": "failed", "error": "timeout"})

    assert restored.status == "failed"
    assert restored.error == "timeout"
    # unset keys still default
    assert restored.candidates == []
    assert restored.retry_count == 0
