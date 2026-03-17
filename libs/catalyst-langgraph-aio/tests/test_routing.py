"""Unit tests for graph routing functions."""

from __future__ import annotations

from langgraph.graph import END

from catalyst_langgraph.graph import (
    _route_after_mention_validation,
    _route_after_proposition_validation,
)


class TestRouteAfterMentionValidation:
    def test_accepted_routes_to_extract_propositions(self):
        state = {"latest_mention_validation": {"verdict": "valid"}, "mention_retry_count": 0, "max_retries": 3}
        assert _route_after_mention_validation(state) == "extract_propositions"

    def test_rejected_with_retries_left_routes_to_repair(self):
        state = {"latest_mention_validation": {"verdict": "invalid"}, "mention_retry_count": 1, "max_retries": 3}
        assert _route_after_mention_validation(state) == "repair_mentions"

    def test_rejected_at_max_retries_routes_to_end(self):
        state = {"latest_mention_validation": {"verdict": "invalid"}, "mention_retry_count": 3, "max_retries": 3}
        assert _route_after_mention_validation(state) == END

    def test_missing_validation_defaults_to_rejected(self):
        state = {"mention_retry_count": 0, "max_retries": 3}
        assert _route_after_mention_validation(state) == "repair_mentions"

    def test_missing_retry_count_defaults_to_zero(self):
        state = {"latest_mention_validation": {"verdict": "invalid"}, "max_retries": 3}
        assert _route_after_mention_validation(state) == "repair_mentions"


class TestRouteAfterPropositionValidation:
    def test_accepted_routes_to_persist(self):
        state = {"latest_proposition_validation": {"verdict": "valid"}, "proposition_retry_count": 0, "max_retries": 3}
        assert _route_after_proposition_validation(state) == "persist_artifacts"

    def test_rejected_with_retries_left_routes_to_repair(self):
        state = {"latest_proposition_validation": {"verdict": "invalid"}, "proposition_retry_count": 1, "max_retries": 3}
        assert _route_after_proposition_validation(state) == "repair_propositions"

    def test_rejected_at_max_retries_routes_to_end(self):
        state = {"latest_proposition_validation": {"verdict": "invalid"}, "proposition_retry_count": 3, "max_retries": 3}
        assert _route_after_proposition_validation(state) == END
