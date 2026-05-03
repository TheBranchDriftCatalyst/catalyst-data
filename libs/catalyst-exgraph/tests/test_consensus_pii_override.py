"""Test ConsensusNode PII quorum override.

Phase B / CD-94ow.  PII types (PHONE_NUMBER, EMAIL, SSN, CREDIT_CARD,
ADDRESS, DOB) should be accepted with K=1 even in a 5-encoder ensemble,
because gliner-pii is the only encoder that reliably surfaces them.
"""

from __future__ import annotations

import pytest
from catalyst_exgraph.consensus_taxonomy import PII_TYPES
from catalyst_exgraph.nodes.consensus import ConsensusNode
from catalyst_exgraph.state import ExGraphState


def _pii_mention(text: str, raw_type: str, encoder: str = "gliner-pii") -> dict:
    return {
        "text": text,
        "mention_type": raw_type,
        "span_start": 0,
        "span_end": len(text),
        "confidence": 0.95,
        "_source_encoder": encoder,
    }


def _state(per_encoder: dict, doc_id: str = "doc-pii") -> ExGraphState:
    return {
        "raw_text": "Call me at 555-1234 or email alice@example.com.",
        "source_metadata": {"document_id": doc_id},
        "doc_id": doc_id,
        "per_encoder_mentions": per_encoder,
        "stages": {},
        "upstream_context": {},
        "audit_events": [],
        "status": "pending",
    }


@pytest.mark.asyncio
async def test_pii_types_set_is_nonempty():
    """PII_TYPES contains the expected canonical values."""
    assert "PHONE_NUMBER" in PII_TYPES
    assert "EMAIL" in PII_TYPES
    assert "SSN" in PII_TYPES
    assert "CREDIT_CARD" in PII_TYPES
    assert "ADDRESS" in PII_TYPES
    assert "DOB" in PII_TYPES


@pytest.mark.asyncio
async def test_phone_number_accepted_with_1_of_5_votes():
    """PHONE_NUMBER found only by gliner-pii (1/5 votes) → accepted (K=1 override)."""
    encoders = ["gliner-medium", "gliner-large", "gliner-pii", "nuextract-2.0-8b", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [],
        "gliner-large": [],
        "gliner-pii": [_pii_mention("555-1234", "phone number")],
        "nuextract-2.0-8b": [],
        "universalner-7b": [],
    }

    node = ConsensusNode(encoders=encoders)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    m = result["consensus_mentions"][0]
    assert m["canonical_type"] == "PHONE_NUMBER"
    assert m["vote_count"] == 1
    assert m["n_encoders"] == 5


@pytest.mark.asyncio
async def test_email_accepted_with_1_of_5_votes():
    """EMAIL found only by gliner-pii → accepted."""
    encoders = ["gliner-medium", "gliner-large", "gliner-pii", "nuextract-2.0-8b", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [],
        "gliner-large": [],
        "gliner-pii": [_pii_mention("alice@example.com", "email address")],
        "nuextract-2.0-8b": [],
        "universalner-7b": [],
    }

    node = ConsensusNode(encoders=encoders)
    result = await node(_state(per_encoder))

    assert len(result["consensus_mentions"]) == 1
    assert result["consensus_mentions"][0]["canonical_type"] == "EMAIL"


@pytest.mark.asyncio
async def test_non_pii_type_not_accepted_with_1_of_5_votes():
    """ORG found by only 1/5 encoders → rejected (default quorum=3)."""
    encoders = ["gliner-medium", "gliner-large", "gliner-pii", "nuextract-2.0-8b", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [
            {
                "text": "Acme",
                "mention_type": "ORG",
                "span_start": 0,
                "span_end": 4,
                "confidence": 0.9,
                "_source_encoder": "gliner-medium",
            }
        ],
        "gliner-large": [],
        "gliner-pii": [],
        "nuextract-2.0-8b": [],
        "universalner-7b": [],
    }

    node = ConsensusNode(encoders=encoders)
    result = await node(_state(per_encoder))

    assert result["consensus_mentions"] == []
    assert len(result["rejected_mentions"]) == 1


@pytest.mark.asyncio
async def test_pii_override_disabled_when_explicit_empty_dict():
    """Passing per_type_quorum={} disables PII special-casing."""
    encoders = ["gliner-medium", "gliner-large", "gliner-pii", "nuextract-2.0-8b", "universalner-7b"]
    per_encoder = {
        "gliner-medium": [],
        "gliner-large": [],
        "gliner-pii": [_pii_mention("555-1234", "phone number")],
        "nuextract-2.0-8b": [],
        "universalner-7b": [],
    }

    # Explicit empty per_type_quorum disables PII override → default quorum=3 applies
    node = ConsensusNode(encoders=encoders, per_type_quorum={})
    result = await node(_state(per_encoder))

    assert result["consensus_mentions"] == []
    assert len(result["rejected_mentions"]) == 1


@pytest.mark.asyncio
async def test_pii_override_can_be_customized():
    """per_type_quorum can raise the threshold for specific PII types."""
    encoders = ["gliner-pii", "gliner-medium"]
    per_encoder = {
        "gliner-pii": [_pii_mention("555-1234", "phone number")],
        "gliner-medium": [],
    }

    # Override PHONE_NUMBER to require K=2
    node = ConsensusNode(encoders=encoders, per_type_quorum={"PHONE_NUMBER": 2})
    result = await node(_state(per_encoder))

    # 1/2 vote < K=2 → rejected
    assert result["consensus_mentions"] == []


@pytest.mark.asyncio
async def test_all_pii_types_have_k1_default():
    """Each PII type defaults to K=1 in ConsensusNode with default per_type_quorum."""
    node = ConsensusNode(encoders=["a", "b", "c", "d", "e"])
    for t in PII_TYPES:
        assert node.per_type_quorum.get(t) == 1, f"{t} should default to K=1"
