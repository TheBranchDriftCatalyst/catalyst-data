"""Test ConsensusNode type voting — majority wins; ties broken by mean confidence.

Phase B / CD-94ow.
"""

from __future__ import annotations

import pytest
from catalyst_exgraph.nodes.consensus import ConsensusNode
from catalyst_exgraph.state import ExGraphState


def _mention(text: str, mention_type: str, encoder: str, span_start: int = 0, confidence: float = 0.9) -> dict:
    return {
        "text": text,
        "mention_type": mention_type,
        "span_start": span_start,
        "span_end": span_start + len(text),
        "confidence": confidence,
        "_source_encoder": encoder,
    }


def _state(per_encoder: dict, doc_id: str = "doc-vote") -> ExGraphState:
    return {
        "raw_text": "NATO met at the UN headquarters.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "per_encoder_mentions": per_encoder,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


@pytest.mark.asyncio
async def test_majority_type_wins():
    """3 encoders: 2 say PERSON, 1 says ORG → PERSON wins."""
    encoders = ["gliner-medium", "gliner-large", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [_mention("NATO", "PERSON", "gliner-medium")],
        "gliner-large": [_mention("NATO", "PERSON", "gliner-large")],
        "universalner-7b": [_mention("NATO", "ORG", "universalner-7b")],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    m = result["consensus_mentions"][0]
    assert m["canonical_type"] == "PERSON"
    assert m["type_votes"] == {"PERSON": 2, "ORG": 1}


@pytest.mark.asyncio
async def test_type_votes_preserved_even_when_one_type_wins():
    """type_votes dict contains all vote counts, not just the winner."""
    encoders = ["gliner-medium", "gliner-large", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [_mention("NATO", "ORG", "gliner-medium")],
        "gliner-large": [_mention("NATO", "ORG", "gliner-large")],
        "universalner-7b": [_mention("NATO", "NORP", "universalner-7b")],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    m = result["consensus_mentions"][0]
    assert "ORG" in m["type_votes"]
    assert "NORP" in m["type_votes"]
    assert m["type_votes"]["ORG"] == 2
    assert m["type_votes"]["NORP"] == 1


@pytest.mark.asyncio
async def test_tie_broken_by_highest_mean_confidence():
    """Tie in type votes → type with higher mean confidence wins."""
    encoders = ["gliner-medium", "gliner-large"]
    per_encoder = {
        "gliner-medium": [_mention("NATO", "NORP", "gliner-medium", confidence=0.5)],
        "gliner-large": [_mention("NATO", "ORG", "gliner-large", confidence=0.95)],
    }

    node = ConsensusNode(encoders=encoders, quorum=1)
    result = await node(_state(per_encoder))

    # Each encoder has 1 vote but vote_count per unique encoder is counted;
    # clustering groups them because same text + overlapping spans.
    # ORG has higher mean confidence (0.95 vs 0.5) → ORG wins.
    m = result["consensus_mentions"][0]
    assert m["canonical_type"] == "ORG"


@pytest.mark.asyncio
async def test_gliner_pii_raw_labels_canonicalized():
    """gliner-pii raw label 'phone number' → canonical 'PHONE_NUMBER' in type_votes."""
    encoders = ["gliner-pii", "gliner-medium"]
    per_encoder = {
        "gliner-pii": [
            {
                "text": "555-1234",
                "mention_type": "phone number",  # raw gliner-pii label
                "span_start": 0,
                "span_end": 8,
                "confidence": 0.98,
                "_source_encoder": "gliner-pii",
            }
        ],
        "gliner-medium": [],
    }

    # PII quorum = 1 (default), so gliner-pii alone should be enough
    node = ConsensusNode(encoders=encoders)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    m = result["consensus_mentions"][0]
    assert m["canonical_type"] == "PHONE_NUMBER"
    assert "PHONE_NUMBER" in m["type_votes"]


@pytest.mark.asyncio
async def test_nuextract_title_case_labels_canonicalized():
    """nuextract-2.0-8b 'Person' label → canonical 'PERSON'."""
    encoders = ["nuextract-2.0-8b", "gliner-medium"]
    per_encoder = {
        "nuextract-2.0-8b": [
            {
                "text": "Alice",
                "mention_type": "Person",  # title-case NuExtract label
                "span_start": 0,
                "span_end": 5,
                "confidence": 0.88,
                "_source_encoder": "nuextract-2.0-8b",
            }
        ],
        "gliner-medium": [_mention("Alice", "PERSON", "gliner-medium")],
    }

    node = ConsensusNode(encoders=encoders, quorum=2)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    m = result["consensus_mentions"][0]
    assert m["canonical_type"] == "PERSON"
    # Both mentions should have been canonicalized to PERSON and merged
    assert m["vote_count"] == 2
