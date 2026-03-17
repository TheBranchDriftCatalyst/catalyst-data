from __future__ import annotations

from catalyst_contracts.models.validation import ValidationVerdict
from catalyst_contracts.validators.concordance_validator import validate_concordance


class TestValidateConcordance:
    def test_valid_candidate_set(self):
        sets = [
            {
                "mention_id": "m1",
                "candidates": [
                    {
                        "entity_id": "e1",
                        "exact": 1.0,
                        "substring": 0.8,
                        "jaccard": 0.6,
                        "cosine": 0.7,
                        "combined": 0.8,
                    }
                ],
            }
        ]
        result = validate_concordance(sets, {"e1", "e2"})
        assert result.verdict == ValidationVerdict.VALID

    def test_unknown_entity(self):
        sets = [
            {
                "mention_id": "m1",
                "candidates": [
                    {
                        "entity_id": "unknown_entity",
                        "exact": 1.0,
                        "substring": 0.8,
                        "jaccard": 0.6,
                        "cosine": 0.7,
                        "combined": 0.8,
                    }
                ],
            }
        ]
        result = validate_concordance(sets, {"e1", "e2"})
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "UNKNOWN_ENTITY" for e in result.errors)

    def test_score_out_of_range(self):
        sets = [
            {
                "mention_id": "m1",
                "candidates": [
                    {
                        "entity_id": "e1",
                        "exact": 1.5,
                        "substring": 0.8,
                        "jaccard": 0.6,
                        "cosine": 0.7,
                        "combined": 0.8,
                    }
                ],
            }
        ]
        result = validate_concordance(sets, {"e1"})
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "SCORE_OUT_OF_RANGE" for e in result.errors)

    def test_inconsistent_combined_warning(self):
        sets = [
            {
                "mention_id": "m1",
                "candidates": [
                    {
                        "entity_id": "e1",
                        "exact": 0.3,
                        "substring": 0.2,
                        "jaccard": 0.1,
                        "cosine": 0.2,
                        "combined": 0.95,
                    }
                ],
            }
        ]
        result = validate_concordance(sets, {"e1"})
        assert len(result.warnings) > 0
        assert any(e.code == "INCONSISTENT_SCORES" for e in result.warnings)

    def test_empty_sets(self):
        result = validate_concordance([], {"e1"})
        assert result.verdict == ValidationVerdict.VALID
