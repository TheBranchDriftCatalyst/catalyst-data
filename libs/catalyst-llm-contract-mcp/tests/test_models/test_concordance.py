from __future__ import annotations

import pytest
from pydantic import ValidationError

from catalyst_contracts.models.concordance import (
    ConcordanceCandidateScore,
    ConcordanceCandidateSet,
)


class TestConcordanceCandidateScore:
    def test_valid_score(self):
        s = ConcordanceCandidateScore(
            entity_id="e1",
            exact=1.0,
            substring=0.8,
            jaccard=0.6,
            cosine=0.7,
            combined=0.8,
        )
        assert s.combined == 0.8

    def test_score_bounds(self):
        with pytest.raises(ValidationError):
            ConcordanceCandidateScore(
                entity_id="e1",
                exact=1.1,
                substring=0.0,
                jaccard=0.0,
                cosine=0.0,
                combined=0.0,
            )

    def test_negative_score(self):
        with pytest.raises(ValidationError):
            ConcordanceCandidateScore(
                entity_id="e1",
                exact=-0.1,
                substring=0.0,
                jaccard=0.0,
                cosine=0.0,
                combined=0.0,
            )


class TestConcordanceCandidateSet:
    def test_valid_set(self):
        cs = ConcordanceCandidateSet(
            mention_id="m1",
            candidates=[
                ConcordanceCandidateScore(
                    entity_id="e1",
                    exact=1.0,
                    substring=0.8,
                    jaccard=0.6,
                    cosine=0.7,
                    combined=0.8,
                ),
            ],
            ambiguity_flag=False,
        )
        assert len(cs.candidates) == 1

    def test_empty_candidates(self):
        cs = ConcordanceCandidateSet(
            mention_id="m1",
            candidates=[],
        )
        assert cs.ambiguity_flag is False
