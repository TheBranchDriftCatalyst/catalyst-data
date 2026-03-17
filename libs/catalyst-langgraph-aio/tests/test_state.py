"""Tests for state model definitions."""

from catalyst_langgraph.state import (
    AuditEvent,
    ExtractionState,
    SourceMetadata,
    WorkflowStatus,
)


def test_workflow_status_values():
    assert WorkflowStatus.PENDING.value == "pending"
    assert WorkflowStatus.EXTRACTING_MENTIONS.value == "extracting_mentions"
    assert WorkflowStatus.VALIDATING_MENTIONS.value == "validating_mentions"
    assert WorkflowStatus.REPAIRING_MENTIONS.value == "repairing_mentions"
    assert WorkflowStatus.EXTRACTING_PROPOSITIONS.value == "extracting_propositions"
    assert WorkflowStatus.VALIDATING_PROPOSITIONS.value == "validating_propositions"
    assert WorkflowStatus.REPAIRING_PROPOSITIONS.value == "repairing_propositions"
    assert WorkflowStatus.PERSISTING.value == "persisting"
    assert WorkflowStatus.COMPLETED.value == "completed"
    assert WorkflowStatus.FAILED.value == "failed"


def test_source_metadata():
    meta = SourceMetadata(
        document_id="doc-1",
        chunk_id="chunk-1",
        source="test-source",
        domain="test-domain",
    )
    assert meta.document_id == "doc-1"
    assert meta.source == "test-source"


def test_audit_event_defaults():
    event = AuditEvent(node_name="extract_mentions", status="completed")
    assert event.node_name == "extract_mentions"
    assert event.status == "completed"
    assert event.timestamp  # should have a default
    assert event.details == {}


def test_extraction_state_as_typed_dict():
    state: ExtractionState = {
        "raw_text": "Hello world",
        "status": "pending",
        "mention_retry_count": 0,
        "proposition_retry_count": 0,
        "max_retries": 3,
        "audit_events": [],
    }
    assert state["raw_text"] == "Hello world"
    assert state["status"] == "pending"
    assert state["max_retries"] == 3
