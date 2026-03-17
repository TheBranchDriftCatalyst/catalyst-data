from __future__ import annotations

from catalyst_contracts.models.validation import ValidationVerdict
from catalyst_contracts.validators.proposition_validator import validate_propositions


class TestValidatePropositions:
    def test_valid_propositions(
        self, valid_propositions_data, known_mention_ids, source_text
    ):
        result = validate_propositions(
            valid_propositions_data, known_mention_ids, source_text
        )
        assert result.verdict == ValidationVerdict.VALID
        assert result.valid_count == 2

    def test_invalid_reference(self, known_mention_ids, source_text):
        props = [
            {
                "kind": "binary",
                "subject_id": "nonexistent",
                "predicate": "knows",
                "object_id": "mention_0",
                "confidence": 0.8,
            }
        ]
        result = validate_propositions(props, known_mention_ids, source_text)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "INVALID_REFERENCE" for e in result.errors)

    def test_nary_invalid_reference(self, known_mention_ids, source_text):
        props = [
            {
                "kind": "nary",
                "predicate": "transfer",
                "arguments": [
                    {"role": "sender", "mention_id": "bad_id"},
                ],
                "confidence": 0.8,
            }
        ]
        result = validate_propositions(props, known_mention_ids, source_text)
        assert result.verdict == ValidationVerdict.INVALID

    def test_non_snake_case_predicate_warning(self, known_mention_ids, source_text):
        props = [
            {
                "kind": "binary",
                "subject_id": "mention_0",
                "predicate": "FoundedIn",
                "object_id": "mention_1",
                "confidence": 0.8,
            }
        ]
        result = validate_propositions(props, known_mention_ids, source_text)
        assert len(result.warnings) > 0
        assert any(e.code == "INVALID_PREDICATE" for e in result.warnings)

    def test_confidence_out_of_range(self, known_mention_ids, source_text):
        props = [
            {
                "kind": "binary",
                "predicate": "knows",
                "confidence": 2.0,
            }
        ]
        result = validate_propositions(props, known_mention_ids, source_text)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "CONFIDENCE_OUT_OF_RANGE" for e in result.errors)

    def test_empty_propositions(self, known_mention_ids, source_text):
        result = validate_propositions([], known_mention_ids, source_text)
        assert result.verdict == ValidationVerdict.VALID

    def test_invalid_object_id_reference(self, known_mention_ids, source_text):
        """Cover line 44: object_id not in known_mention_ids."""
        props = [
            {
                "kind": "binary",
                "subject_id": "mention_0",
                "predicate": "knows",
                "object_id": "nonexistent_object",
                "confidence": 0.8,
            }
        ]
        result = validate_propositions(props, known_mention_ids, source_text)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(
            e.code == "INVALID_REFERENCE" and "object_id" in e.path
            for e in result.errors
        )

    def test_ambiguous_verdict_mixed(self, known_mention_ids, source_text):
        """Cover line 105: mix of valid and invalid propositions yields AMBIGUOUS."""
        props = [
            {
                "kind": "binary",
                "subject_id": "mention_0",
                "predicate": "knows",
                "object_id": "mention_1",
                "confidence": 0.8,
            },
            {
                "kind": "binary",
                "subject_id": "nonexistent",
                "predicate": "likes",
                "object_id": "mention_1",
                "confidence": 0.8,
            },
        ]
        result = validate_propositions(props, known_mention_ids, source_text)
        assert result.verdict == ValidationVerdict.AMBIGUOUS
        assert result.valid_count == 1
        assert result.invalid_count == 1


class TestMentionIdEdgeCases:
    """Edge-case tests for mention ID validation in propositions."""

    SOURCE = "The cat sat on the mat."
    KNOWN_IDS = {"m-cat", "m-mat"}

    def _prop(self, **overrides):
        base = {
            "kind": "binary",
            "predicate": "sits_on",
            "confidence": 0.9,
        }
        base.update(overrides)
        return base

    def test_subject_id_not_in_known_ids_rejected(self):
        """subject_id present but not in known_ids -> rejected."""
        props = [self._prop(subject_id="m-nonexistent", object_id="m-mat")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.INVALID
        assert result.invalid_count == 1
        assert any(
            e.code == "INVALID_REFERENCE" and "subject_id" in e.path
            for e in result.errors
        )

    def test_object_id_not_in_known_ids_rejected(self):
        """object_id present but not in known_ids -> rejected."""
        props = [self._prop(subject_id="m-cat", object_id="m-nonexistent")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.INVALID
        assert result.invalid_count == 1
        assert any(
            e.code == "INVALID_REFERENCE" and "object_id" in e.path
            for e in result.errors
        )

    def test_no_mention_ids_passes(self):
        """Both subject_id and object_id missing/None -> accepted."""
        props = [self._prop()]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.VALID
        assert result.valid_count == 1
        assert result.errors == []

    def test_none_mention_ids_passes(self):
        """Explicit None values for subject_id and object_id -> accepted."""
        props = [self._prop(subject_id=None, object_id=None)]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.VALID
        assert result.valid_count == 1

    def test_empty_string_subject_id_rejected(self):
        """Empty string subject_id -> rejected (not a valid reference)."""
        props = [self._prop(subject_id="", object_id="m-mat")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(
            e.code == "INVALID_REFERENCE" and "subject_id" in e.path
            for e in result.errors
        )

    def test_subject_valid_object_invalid_rejects_with_object_error(self):
        """subject_id valid, object_id invalid -> rejected with error on object only."""
        props = [self._prop(subject_id="m-cat", object_id="m-bogus")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.INVALID
        assert result.invalid_count == 1
        assert len(result.errors) == 1
        assert "object_id" in result.errors[0].path

    def test_both_ids_invalid_two_errors(self):
        """Both subject_id and object_id invalid -> rejected with two errors."""
        props = [self._prop(subject_id="m-bad-subj", object_id="m-bad-obj")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.INVALID
        assert result.invalid_count == 1
        assert len(result.errors) == 2
        error_paths = {e.path for e in result.errors}
        assert any("subject_id" in p for p in error_paths)
        assert any("object_id" in p for p in error_paths)

    def test_both_ids_valid_accepted(self):
        """Both subject_id and object_id in known_ids -> accepted."""
        props = [self._prop(subject_id="m-cat", object_id="m-mat")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.VALID
        assert result.valid_count == 1
        assert result.errors == []

    def test_empty_known_ids_no_references_accepted(self):
        """known_mention_ids is empty but no IDs referenced -> accepted."""
        props = [self._prop()]
        result = validate_propositions(props, set(), self.SOURCE)
        assert result.verdict == ValidationVerdict.VALID
        assert result.valid_count == 1

    def test_empty_known_ids_with_references_rejected(self):
        """known_mention_ids is empty but IDs are referenced -> rejected."""
        props = [self._prop(subject_id="m-cat")]
        result = validate_propositions(props, set(), self.SOURCE)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(e.code == "INVALID_REFERENCE" for e in result.errors)

    def test_unknown_subject_mention_id_rejected(self):
        """subject_mention_id referencing unknown ID should be rejected."""
        props = [self._prop(subject_mention_id="m-nonexistent", object_id="m-mat")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(
            e.code == "INVALID_REFERENCE" and "subject_mention_id" in e.path
            for e in result.errors
        )

    def test_unknown_object_mention_id_rejected(self):
        """object_mention_id referencing unknown ID should be rejected."""
        props = [self._prop(subject_id="m-cat", object_mention_id="m-nonexistent")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.INVALID
        assert any(
            e.code == "INVALID_REFERENCE" and "object_mention_id" in e.path
            for e in result.errors
        )

    def test_valid_subject_mention_id_accepted(self):
        """subject_mention_id referencing a known ID should pass."""
        props = [self._prop(subject_mention_id="m-cat", object_id="m-mat")]
        result = validate_propositions(props, self.KNOWN_IDS, self.SOURCE)
        assert result.verdict == ValidationVerdict.VALID
        assert result.valid_count == 1
