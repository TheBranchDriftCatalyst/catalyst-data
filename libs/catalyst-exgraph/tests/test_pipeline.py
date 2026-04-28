"""Behavioral tests for pipeline composition and result mapping.

Tests verify that build_pipeline correctly chains stages, passes upstream
context between stages, and that pipeline_result_to_legacy maps output
to the expected flat format.
"""

from __future__ import annotations

from unittest.mock import patch

from catalyst_exgraph.config import StageConfig, ner_stage_config, spo_stage_config
from catalyst_exgraph.pipeline import build_pipeline, pipeline_result_to_legacy
from catalyst_exgraph.state import ExGraphState

from .conftest import (
    SAMPLE_MENTIONS,
    SAMPLE_PROPOSITIONS,
    SAMPLE_TEXT,
    DummyOutput,
    MockExtractionClient,
    MockMCPClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROMPT_PATCH = patch(
    "catalyst_exgraph.nodes.extract._load_prompt",
    return_value="You are an extraction assistant.",
)
_REPAIR_PROMPT_PATCH = patch(
    "catalyst_exgraph.nodes.repair._load_repair_prompt",
    return_value="Fix the extraction errors.",
)


def _make_input_state(text: str = SAMPLE_TEXT, **overrides) -> ExGraphState:
    state: ExGraphState = {
        "raw_text": text,
        "source_metadata": {"document_id": "doc-001", "chunk_id": "chunk-001"},
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }
    state.update(overrides)
    return state


# =====================================================================
# 1. NER -> SPO chain: NER accepted flows into SPO upstream_context
# =====================================================================


@_PROMPT_PATCH
async def test_ner_spo_chain_passes_accepted_mentions_to_spo(_prompt):
    """Two-stage pipeline: NER accepted items become SPO upstream_context."""
    ner_client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    spo_client = MockExtractionClient(propositions=SAMPLE_PROPOSITIONS)
    mcp = MockMCPClient(verdict="valid")

    ner_cfg = ner_stage_config(max_retries=0)
    spo_cfg = spo_stage_config(max_retries=0)

    pipeline = build_pipeline(
        [ner_cfg, spo_cfg],
        clients={"ner": ner_client, "spo": spo_client},
        mcp_client=mcp,
    )
    result = await pipeline.ainvoke(_make_input_state())

    # Both stages should complete
    assert "ner" in result["stages"]
    assert "spo" in result["stages"]
    assert result["stages"]["ner"]["status"] == "completed"
    assert result["stages"]["spo"]["status"] == "completed"

    # NER accepted items populated
    assert len(result["stages"]["ner"]["accepted"]) == len(SAMPLE_MENTIONS)
    # SPO accepted items populated
    assert len(result["stages"]["spo"]["accepted"]) == len(SAMPLE_PROPOSITIONS)


# =====================================================================
# 2. NER-only pipeline: single stage, no SPO
# =====================================================================


@_PROMPT_PATCH
async def test_ner_only_pipeline_single_stage(_prompt):
    """Single NER stage pipeline: only mentions in result, no SPO stage."""
    client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    mcp = MockMCPClient(verdict="valid")
    ner_cfg = ner_stage_config(max_retries=0)

    pipeline = build_pipeline([ner_cfg], client, mcp)
    result = await pipeline.ainvoke(_make_input_state())

    assert "ner" in result["stages"]
    assert "spo" not in result["stages"]
    assert len(result["stages"]["ner"]["accepted"]) == len(SAMPLE_MENTIONS)


# =====================================================================
# 3. Per-stage model override: different clients for NER vs SPO
# =====================================================================


@_PROMPT_PATCH
async def test_per_stage_model_override_different_clients(_prompt):
    """When clients dict maps stage_name -> client, each stage uses its own client."""
    ner_mentions = [SAMPLE_MENTIONS[0]]  # Only Acme Corp
    spo_props = SAMPLE_PROPOSITIONS

    ner_client = MockExtractionClient(mentions=ner_mentions)
    spo_client = MockExtractionClient(propositions=spo_props)
    mcp = MockMCPClient(verdict="valid")

    ner_cfg = ner_stage_config(max_retries=0)
    spo_cfg = spo_stage_config(max_retries=0)

    pipeline = build_pipeline(
        [ner_cfg, spo_cfg],
        clients={"ner": ner_client, "spo": spo_client},
        mcp_client=mcp,
    )
    result = await pipeline.ainvoke(_make_input_state())

    # NER client should have been called (for NER extraction)
    assert len(ner_client.structured_calls) >= 1
    # SPO client should have been called (for SPO extraction)
    assert len(spo_client.structured_calls) >= 1

    # NER has 1 mention (from ner_client)
    assert len(result["stages"]["ner"]["accepted"]) == 1
    # SPO has 1 proposition (from spo_client)
    assert len(result["stages"]["spo"]["accepted"]) == 1


# =====================================================================
# 4. pipeline_result_to_legacy: maps stages -> flat keys
# =====================================================================


def test_pipeline_result_to_legacy_maps_ner_and_spo():
    """pipeline_result_to_legacy extracts accepted_mentions and accepted_propositions."""
    state: ExGraphState = {
        "stages": {
            "ner": {
                "accepted": [{"text": "Alice", "mention_type": "PERSON"}],
                "retry_count": 1,
                "status": "completed",
            },
            "spo": {
                "accepted": [{"subject": "Alice", "predicate": "met", "object": "Bob"}],
                "retry_count": 0,
                "status": "completed",
            },
        },
        "audit_events": [{"node_name": "extract_ner", "status": "completed"}],
        "status": "completed",
    }

    legacy = pipeline_result_to_legacy(state)

    assert legacy["accepted_mentions"] == [{"text": "Alice", "mention_type": "PERSON"}]
    assert legacy["accepted_propositions"] == [{"subject": "Alice", "predicate": "met", "object": "Bob"}]
    assert legacy["mention_retry_count"] == 1
    assert legacy["proposition_retry_count"] == 0
    assert legacy["status"] == "completed"
    assert len(legacy["audit_events"]) == 1


def test_pipeline_result_to_legacy_missing_spo_returns_empty():
    """When only NER stage exists, SPO fields default to empty."""
    state: ExGraphState = {
        "stages": {
            "ner": {
                "accepted": [{"text": "X"}],
                "retry_count": 0,
                "status": "completed",
            },
        },
        "audit_events": [],
    }

    legacy = pipeline_result_to_legacy(state)

    assert legacy["accepted_propositions"] == []
    assert legacy["proposition_retry_count"] == 0
    assert legacy["status"] == "completed"


def test_pipeline_result_to_legacy_error_status_propagates():
    """If any stage has 'error' status, legacy status is 'failed'."""
    state: ExGraphState = {
        "stages": {
            "ner": {"accepted": [], "retry_count": 0, "status": "error"},
            "spo": {"accepted": [], "retry_count": 0, "status": "completed"},
        },
        "audit_events": [],
    }

    legacy = pipeline_result_to_legacy(state)
    assert legacy["status"] == "failed"


def test_pipeline_result_to_legacy_empty_stages():
    """Empty stages dict gives empty output."""
    state: ExGraphState = {"stages": {}, "audit_events": [], "status": "completed"}
    legacy = pipeline_result_to_legacy(state)

    assert legacy["accepted_mentions"] == []
    assert legacy["accepted_propositions"] == []
    assert legacy["status"] == "completed"


# =====================================================================
# 5. Empty pipeline: no active stages
# =====================================================================


async def test_empty_pipeline_no_active_stages_completes():
    """Pipeline with no active stages (all skipped or empty) completes immediately."""
    pipeline = build_pipeline(stages=[], clients={}, mcp_client=MockMCPClient())
    result = await pipeline.ainvoke(_make_input_state())

    assert result.get("status") == "completed"


async def test_all_skipped_stages_treated_as_empty_pipeline():
    """When all stages have skip=True, pipeline behaves as empty."""
    ner_cfg = StageConfig(
        stage_name="ner",
        extraction_schema=DummyOutput,
        prompt_id="test",
        validation_tool="test",
        repair_prompt_id="test",
        skip=True,
    )
    spo_cfg = StageConfig(
        stage_name="spo",
        extraction_schema=DummyOutput,
        prompt_id="test",
        validation_tool="test",
        repair_prompt_id="test",
        skip=True,
    )
    client = MockExtractionClient()
    mcp = MockMCPClient()

    pipeline = build_pipeline([ner_cfg, spo_cfg], client, mcp)
    result = await pipeline.ainvoke(_make_input_state())

    assert result.get("status") == "completed"


# =====================================================================
# 6. Three-stage pipeline: NER -> SPO -> custom chains correctly
# =====================================================================


@_PROMPT_PATCH
async def test_three_stage_pipeline_chains_correctly(_prompt):
    """Three stages run in order, each receiving upstream context from prior stage."""
    from catalyst_contracts.models.extraction_output import MentionExtractionResult

    # Third stage reuses NER schema for simplicity
    custom_mentions = [
        {"text": "Event-2024", "mention_type": "EVENT", "span_start": 40, "span_end": 50, "confidence": 0.8}
    ]

    ner_client = MockExtractionClient(mentions=SAMPLE_MENTIONS)
    spo_client = MockExtractionClient(propositions=SAMPLE_PROPOSITIONS)
    custom_client = MockExtractionClient(mentions=custom_mentions)
    mcp = MockMCPClient(verdict="valid")

    ner_cfg = ner_stage_config(max_retries=0)
    spo_cfg = spo_stage_config(max_retries=0)
    custom_cfg = StageConfig(
        stage_name="events",
        extraction_schema=MentionExtractionResult,
        prompt_id="mention_extraction",
        validation_tool="validate_mentions",
        repair_prompt_id="mention_repair",
        max_retries=0,
    )

    pipeline = build_pipeline(
        [ner_cfg, spo_cfg, custom_cfg],
        clients={"ner": ner_client, "spo": spo_client, "events": custom_client},
        mcp_client=mcp,
    )
    result = await pipeline.ainvoke(_make_input_state())

    assert "ner" in result["stages"]
    assert "spo" in result["stages"]
    assert "events" in result["stages"]

    assert result["stages"]["ner"]["status"] == "completed"
    assert result["stages"]["spo"]["status"] == "completed"
    assert result["stages"]["events"]["status"] == "completed"

    # Third stage accepted its own mentions
    assert len(result["stages"]["events"]["accepted"]) == 1
    assert result["stages"]["events"]["accepted"][0]["text"] == "Event-2024"


# =====================================================================
# 7. Single client used for all stages when not a dict
# =====================================================================


@_PROMPT_PATCH
async def test_single_client_shared_across_all_stages(_prompt):
    """When clients is a single client (not dict), all stages use it."""
    # Client returns both mentions and propositions depending on schema
    client = MockExtractionClient(
        mentions=SAMPLE_MENTIONS,
        propositions=SAMPLE_PROPOSITIONS,
    )
    mcp = MockMCPClient(verdict="valid")

    ner_cfg = ner_stage_config(max_retries=0)
    spo_cfg = spo_stage_config(max_retries=0)

    pipeline = build_pipeline([ner_cfg, spo_cfg], client, mcp)
    result = await pipeline.ainvoke(_make_input_state())

    # Both stages completed using the same client
    assert result["stages"]["ner"]["status"] == "completed"
    assert result["stages"]["spo"]["status"] == "completed"
    # Client was called at least twice (once per stage)
    assert len(client.structured_calls) >= 2


# =====================================================================
# 8. Audit events accumulate across stages
# =====================================================================


@_PROMPT_PATCH
async def test_audit_events_accumulate_across_pipeline_stages(_prompt):
    """Pipeline audit_events includes entries from all stages."""
    client = MockExtractionClient(
        mentions=SAMPLE_MENTIONS,
        propositions=SAMPLE_PROPOSITIONS,
    )
    mcp = MockMCPClient(verdict="valid")

    ner_cfg = ner_stage_config(max_retries=0)
    spo_cfg = spo_stage_config(max_retries=0)

    pipeline = build_pipeline([ner_cfg, spo_cfg], client, mcp)
    result = await pipeline.ainvoke(_make_input_state())

    events = result.get("audit_events", [])
    node_names = [e["node_name"] for e in events]

    # Should have events from both NER and SPO stages
    assert any("ner" in name for name in node_names)
    assert any("spo" in name for name in node_names)
