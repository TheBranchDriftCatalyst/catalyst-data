"""Test that ExtractNode builds an SPO prompt with full consensus provenance.

Phase D / CD-3w3n.  Verifies that when upstream_context.accepted_mentions
carries ConsensusMention dicts (with vote_count / n_encoders / mean_confidence),
the assembled prompt string contains the per-entity provenance block.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from catalyst_contracts.models.extraction_output import (
    PropositionExtractionResult,
)
from catalyst_exgraph.config import spo_stage_config
from catalyst_exgraph.nodes.extract import ExtractNode
from catalyst_exgraph.state import ExGraphState
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _consensus_mention(
    text: str,
    canonical_type: str = "PERSON",
    vote_count: int = 5,
    n_encoders: int = 5,
    mean_confidence: float = 0.94,
) -> dict:
    """Build a ConsensusMention-shaped dict."""
    return {
        "mention_id": f"test-{text.lower().replace(' ', '-')}",
        "text": text,
        "canonical_type": canonical_type,
        "vote_count": vote_count,
        "n_encoders": n_encoders,
        "mean_confidence": mean_confidence,
        "source_models": ["enc-a", "enc-b"],
        "type_votes": {canonical_type: vote_count},
        "span_start": 0,
        "span_end": len(text),
        "span_provenance": "enc-a",
    }


def _spo_state(mentions: list[dict], raw_text: str = "Reagan met Putin at Crimea.") -> ExGraphState:
    return {
        "raw_text": raw_text,
        "source_metadata": {"document_id": "doc-d", "chunk_id": "chunk-d"},
        "stages": {},
        "upstream_context": {"accepted_mentions": mentions},
        "audit_events": [],
        "status": "pending",
    }


class _CapturingClient:
    """Extraction client that records messages passed to structured_output."""

    model: str = "mock"
    structured_method: str = "mock"

    def __init__(self) -> None:
        self.captured_messages: list[Any] = []

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        self.captured_messages = messages
        return PropositionExtractionResult(propositions=[])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="You are an SPO extractor.")
async def test_prompt_contains_vote_fraction(_load_prompt):
    """Fully-voted mention produces 'X/Y votes' in the prompt."""
    mentions = [_consensus_mention("Reagan", vote_count=5, n_encoders=5)]
    client = _CapturingClient()
    node = ExtractNode(config=spo_stage_config(max_retries=0), client=client)

    await node(_spo_state(mentions))

    assert client.captured_messages, "No messages captured"
    human_content = client.captured_messages[-1].content
    assert "5/5 votes" in human_content, f"Expected '5/5 votes' in prompt; got:\n{human_content}"


@pytest.mark.asyncio
@patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="You are an SPO extractor.")
async def test_prompt_contains_mean_conf(_load_prompt):
    """mean_confidence is formatted to 2 decimal places in the prompt."""
    mentions = [_consensus_mention("Reagan", mean_confidence=0.94)]
    client = _CapturingClient()
    node = ExtractNode(config=spo_stage_config(max_retries=0), client=client)

    await node(_spo_state(mentions))

    human_content = client.captured_messages[-1].content
    assert "mean_conf 0.94" in human_content, f"Expected 'mean_conf 0.94'; got:\n{human_content}"


@pytest.mark.asyncio
@patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="You are an SPO extractor.")
async def test_prompt_contains_canonical_type(_load_prompt):
    """canonical_type label appears in the prompt entity block."""
    mentions = [
        _consensus_mention("Reagan", canonical_type="PERSON"),
        _consensus_mention("Crimea", canonical_type="LOCATION", vote_count=3, mean_confidence=0.62),
    ]
    client = _CapturingClient()
    node = ExtractNode(config=spo_stage_config(max_retries=0), client=client)

    await node(_spo_state(mentions))

    human_content = client.captured_messages[-1].content
    assert "PERSON" in human_content, f"Expected 'PERSON' in prompt; got:\n{human_content}"
    assert "LOCATION" in human_content, f"Expected 'LOCATION' in prompt; got:\n{human_content}"


@pytest.mark.asyncio
@patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="You are an SPO extractor.")
async def test_prompt_multi_mention_3_encoder_ensemble(_load_prompt):
    """3-encoder ensemble: three mentions render correctly in the entity block."""
    mentions = [
        _consensus_mention("Reagan", canonical_type="PERSON", vote_count=3, n_encoders=3, mean_confidence=0.94),
        _consensus_mention("Putin", canonical_type="PERSON", vote_count=2, n_encoders=3, mean_confidence=0.87),
        _consensus_mention("Crimea", canonical_type="LOCATION", vote_count=1, n_encoders=3, mean_confidence=0.41),
    ]
    client = _CapturingClient()
    node = ExtractNode(config=spo_stage_config(max_retries=0), client=client)

    await node(_spo_state(mentions))

    human_content = client.captured_messages[-1].content
    assert "3/3 votes" in human_content
    assert "2/3 votes" in human_content
    assert "1/3 votes" in human_content
    assert "mean_conf 0.94" in human_content
    assert "mean_conf 0.87" in human_content
    assert "mean_conf 0.41" in human_content
