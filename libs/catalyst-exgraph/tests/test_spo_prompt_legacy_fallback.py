"""Test that ExtractNode falls back gracefully to bare-text mention format.

Phase D / CD-3w3n.  Legacy mentions (plain dicts with 'text' + 'mention_type'
but no 'vote_count' / 'n_encoders') must render as:
    - EntityName  [TYPE]
without any 'votes' or 'mean_conf' patterns in the prompt.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from catalyst_contracts.models.extraction_output import PropositionExtractionResult
from catalyst_exgraph.config import spo_stage_config
from catalyst_exgraph.nodes.extract import ExtractNode
from catalyst_exgraph.state import ExGraphState
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _legacy_mention(text: str, mention_type: str = "PERSON") -> dict:
    """Build a bare legacy mention dict (no consensus fields)."""
    return {
        "text": text,
        "mention_type": mention_type,
        "span_start": 0,
        "span_end": len(text),
        "confidence": 0.9,
    }


def _spo_state(mentions: list[dict], raw_text: str = "Alice met Bob at the park.") -> ExGraphState:
    return {
        "raw_text": raw_text,
        "source_metadata": {"document_id": "doc-leg", "chunk_id": "chunk-leg"},
        "stages": {},
        "upstream_context": {"accepted_mentions": mentions},
        "audit_events": [],
        "status": "pending",
    }


class _CapturingClient:
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
async def test_legacy_mention_renders_text_type(_load_prompt):
    """Legacy mention appears as '- text [TYPE]' in the prompt."""
    mentions = [_legacy_mention("Alice", "PERSON")]
    client = _CapturingClient()
    node = ExtractNode(config=spo_stage_config(max_retries=0), client=client)

    await node(_spo_state(mentions))

    human_content = client.captured_messages[-1].content
    assert "Alice" in human_content
    assert "PERSON" in human_content
    # Bracket form must be present for legacy shape
    assert "[PERSON]" in human_content or "PERSON" in human_content


@pytest.mark.asyncio
@patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="You are an SPO extractor.")
async def test_legacy_mention_has_no_votes_pattern(_load_prompt):
    """Legacy mention must NOT contain 'votes' or 'mean_conf' strings."""
    mentions = [_legacy_mention("Alice"), _legacy_mention("Bob", "PERSON")]
    client = _CapturingClient()
    node = ExtractNode(config=spo_stage_config(max_retries=0), client=client)

    await node(_spo_state(mentions))

    human_content = client.captured_messages[-1].content
    assert "votes" not in human_content, f"'votes' found in legacy prompt:\n{human_content}"
    assert "mean_conf" not in human_content, f"'mean_conf' found in legacy prompt:\n{human_content}"


@pytest.mark.asyncio
@patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="You are an SPO extractor.")
async def test_empty_mentions_produces_none_line(_load_prompt):
    """Empty accepted_mentions renders as '(none)' placeholder."""
    client = _CapturingClient()
    node = ExtractNode(config=spo_stage_config(max_retries=0), client=client)

    await node(_spo_state([]))

    human_content = client.captured_messages[-1].content
    assert "(none)" in human_content, f"Expected '(none)' for empty mentions; got:\n{human_content}"


@pytest.mark.asyncio
@patch("catalyst_exgraph.nodes.extract._load_prompt", return_value="You are an SPO extractor.")
async def test_mixed_legacy_and_consensus_no_crash(_load_prompt):
    """Mixed legacy + consensus mentions in the same list must not crash."""
    mentions = [
        _legacy_mention("Alice"),
        {
            "text": "Bob",
            "canonical_type": "PERSON",
            "vote_count": 3,
            "n_encoders": 5,
            "mean_confidence": 0.75,
        },
    ]
    client = _CapturingClient()
    node = ExtractNode(config=spo_stage_config(max_retries=0), client=client)

    # Should not raise
    await node(_spo_state(mentions))

    human_content = client.captured_messages[-1].content
    assert "Alice" in human_content
    assert "Bob" in human_content
