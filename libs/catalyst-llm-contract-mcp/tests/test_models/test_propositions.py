from __future__ import annotations

import pytest
from pydantic import ValidationError

from catalyst_contracts.models.propositions import (
    BinaryProposition,
    NaryProposition,
    PropositionArgument,
    PropositionExtraction,
)


class TestBinaryProposition:
    def test_valid_binary(self):
        p = BinaryProposition(
            subject_text="Alice",
            subject_id="m1",
            predicate="knows",
            object_text="Bob",
            object_id="m2",
            confidence=0.9,
        )
        assert p.kind == "binary"
        assert p.negated is False
        assert p.hedged is False
        assert p.qualifiers == {}

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            BinaryProposition(
                predicate="knows",
                confidence=1.5,
            )


class TestNaryProposition:
    def test_valid_nary(self):
        p = NaryProposition(
            predicate="transfer",
            arguments=[
                PropositionArgument(role="sender", text="Alice"),
                PropositionArgument(role="receiver", mention_id="m2"),
                PropositionArgument(role="amount", text="$100"),
            ],
            confidence=0.85,
        )
        assert p.kind == "nary"
        assert len(p.arguments) == 3

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            NaryProposition(
                predicate="x",
                arguments=[],
                confidence=-0.1,
            )


class TestPropositionExtraction:
    def test_binary_extraction(self):
        pe = PropositionExtraction(
            proposition=BinaryProposition(
                predicate="leads", confidence=0.9
            )
        )
        assert pe.proposition.kind == "binary"

    def test_nary_extraction(self):
        pe = PropositionExtraction(
            proposition=NaryProposition(
                predicate="exchange",
                arguments=[PropositionArgument(role="agent", text="X")],
                confidence=0.7,
            )
        )
        assert pe.proposition.kind == "nary"

    def test_discriminator_from_dict(self):
        pe = PropositionExtraction.model_validate({
            "proposition": {
                "kind": "binary",
                "predicate": "knows",
                "confidence": 0.9,
            }
        })
        assert pe.proposition.kind == "binary"

        pe2 = PropositionExtraction.model_validate({
            "proposition": {
                "kind": "nary",
                "predicate": "transfer",
                "arguments": [{"role": "agent", "text": "A"}],
                "confidence": 0.8,
            }
        })
        assert pe2.proposition.kind == "nary"
