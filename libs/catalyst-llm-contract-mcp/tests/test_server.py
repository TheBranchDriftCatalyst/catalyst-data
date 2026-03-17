from __future__ import annotations

import pytest

from catalyst_contracts.audit.repository import AuditRepository
from catalyst_contracts.server import (
    generate_repair_instructions,
    get_contract_schemas,
    validate_concordance_candidates,
    validate_math_propositions,
    validate_mentions,
    validate_propositions,
    validate_spatial_grounding,
)


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    """Redirect audit writes to tmp_path so tests don't leak to ~/.catalyst/."""
    import catalyst_contracts.server as srv

    monkeypatch.setattr(srv, "audit", AuditRepository(tmp_path / "audit.jsonl"))


class TestServerTools:
    def test_get_contract_schemas(self):
        schemas = get_contract_schemas()
        assert isinstance(schemas, dict)
        assert "EvidenceSpan" in schemas
        assert "MentionExtraction" in schemas
        assert "BinaryProposition" in schemas
        assert "ValidationResult" in schemas
        assert "RepairPlan" in schemas

    def test_validate_mentions_valid(self, source_text, valid_mentions_data):
        result = validate_mentions(
            mentions=valid_mentions_data,
            source_text=source_text,
            document_id="doc1",
        )
        assert result["verdict"] == "valid"

    def test_validate_mentions_invalid(self, source_text, invalid_mentions_data):
        result = validate_mentions(
            mentions=invalid_mentions_data,
            source_text=source_text,
            document_id="doc1",
        )
        assert result["verdict"] == "invalid"

    def test_validate_propositions_valid(
        self, valid_propositions_data, source_text
    ):
        result = validate_propositions(
            propositions=valid_propositions_data,
            known_mention_ids=["mention_0", "mention_1", "mention_2"],
            source_text=source_text,
        )
        assert result["verdict"] == "valid"

    def test_validate_spatial_grounding_valid(self, source_text):
        result = validate_spatial_grounding(
            candidates=[
                {
                    "mention_id": "m1",
                    "lat": 40.7128,
                    "lon": -74.0060,
                    "confidence": 0.9,
                }
            ],
            source_text=source_text,
        )
        assert result["verdict"] == "valid"

    def test_validate_math_propositions_valid(self):
        result = validate_math_propositions(
            propositions=[
                {
                    "kind": "equation",
                    "statement": "x = 1",
                    "objects": [{"symbol": "x", "kind": "variable"}],
                }
            ]
        )
        assert result["verdict"] == "valid"

    def test_validate_concordance_candidates_valid(self):
        result = validate_concordance_candidates(
            candidate_sets=[
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
            ],
            known_entity_ids=["e1"],
        )
        assert result["verdict"] == "valid"

    def test_generate_repair_instructions(self):
        vr = {
            "verdict": "invalid",
            "valid_count": 0,
            "invalid_count": 1,
            "errors": [
                {
                    "path": "mentions[0].confidence",
                    "code": "CONFIDENCE_OUT_OF_RANGE",
                    "message": "Out of range",
                }
            ],
            "warnings": [],
            "valid_items": [],
            "invalid_items": [0],
        }
        payload = {"mentions": [{"confidence": 1.5}]}
        result = generate_repair_instructions(
            validation_result=vr,
            original_payload=payload,
        )
        assert "instructions" in result
        assert len(result["instructions"]) == 1
        assert result["instructions"][0]["action"] == "coerce"


class TestServerToolsNegativePaths:
    """Negative and edge-case tests for server tool functions."""

    def test_validate_mentions_empty_list(self, source_text):
        result = validate_mentions(
            mentions=[], source_text=source_text, document_id="doc1"
        )
        assert result["verdict"] == "invalid"
        assert result["valid_count"] == 0
        assert any(e["code"] == "EMPTY_EXTRACTION" for e in result["errors"])

    def test_validate_propositions_empty_list(self, source_text):
        result = validate_propositions(
            propositions=[], known_mention_ids=[], source_text=source_text
        )
        assert result["verdict"] == "valid"

    def test_validate_spatial_empty_list(self, source_text):
        result = validate_spatial_grounding(candidates=[], source_text=source_text)
        assert result["verdict"] == "valid"

    def test_validate_math_empty_list(self):
        result = validate_math_propositions(propositions=[])
        assert result["verdict"] == "valid"

    def test_validate_concordance_empty_list(self):
        result = validate_concordance_candidates(
            candidate_sets=[], known_entity_ids=[]
        )
        assert result["verdict"] == "valid"

    def test_audit_records_written(self, tmp_path, source_text, valid_mentions_data):
        """Verify that audit entries are written on validation calls."""
        import catalyst_contracts.server as srv

        validate_mentions(
            mentions=valid_mentions_data, source_text=source_text, document_id="doc1"
        )
        entries = srv.audit.read_all()
        assert len(entries) >= 1
        assert entries[-1]["tool_name"] == "validate_mentions"
