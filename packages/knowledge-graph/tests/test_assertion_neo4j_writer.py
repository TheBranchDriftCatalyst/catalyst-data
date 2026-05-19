"""Tests for the AMR-rich Statement-node writer in GraphDBResource.

Wave 1, Step 5 (bead ``llm-g0b``): the Neo4j writer now persists the unified
``Assertion`` contract — including ``amr_frame``, ``polarity``, ``modality``,
``qualifiers``, ``t_valid_from/until``, ``is_atemporal``, entity + mention
pointers, and provenance pointers — to ``:Statement`` nodes keyed on
``assertion_id``.

These tests mock the ``neo4j.GraphDatabase`` driver and assert on the Cypher /
params the writer emits. No live Neo4j is required.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

# ``neo4j`` is an optional install in some local environments — provide a
# minimal stub so ``knowledge_graph.resources`` imports cleanly. Tests that
# need the driver subclass ``GraphDBResource`` and inject a fake.
if "neo4j" not in sys.modules:
    _stub_neo4j = types.ModuleType("neo4j")

    class _StubDriver:
        @staticmethod
        def driver(*_args, **_kwargs):  # pragma: no cover — replaced by tests
            raise RuntimeError("real neo4j driver should never be called in unit tests")

    _stub_neo4j.GraphDatabase = _StubDriver  # type: ignore[attr-defined]
    sys.modules["neo4j"] = _stub_neo4j

# ``psycopg`` is also lazy-imported in resources.py (PG paths). Stub it so
# import doesn't blow up in CI without postgres.
if "psycopg" not in sys.modules:
    _stub_psycopg = types.ModuleType("psycopg")
    _stub_psycopg.connect = MagicMock(name="psycopg.connect")  # type: ignore[attr-defined]
    sys.modules["psycopg"] = _stub_psycopg

from knowledge_graph.resources import GraphDBResource  # noqa: E402 — after stubs


# ---------------------------------------------------------------------------
# Test double — captures every Cypher call so we can assert on it
# ---------------------------------------------------------------------------


_captured_runs: list[tuple[str, dict]] = []


class _FakeDriver:
    """Stand-in for ``neo4j.Driver`` that records every session.run call."""

    def __init__(self) -> None:
        self.closed = False

    def session(self):
        session_mock = MagicMock(name="neo4j_session")

        def _run(cypher: str, **params):
            _captured_runs.append((cypher, params))
            return MagicMock()

        session_mock.run.side_effect = _run
        # ``with driver.session() as session:`` plumbing
        ctx = MagicMock()
        ctx.__enter__.return_value = session_mock
        ctx.__exit__.return_value = False
        return ctx

    def close(self) -> None:
        self.closed = True


class _FakeGraphDB(GraphDBResource):
    """``_neo4j_driver`` returns the recording fake."""

    def _neo4j_driver(self):  # type: ignore[override]
        return _FakeDriver()


def _make_resource() -> _FakeGraphDB:
    _captured_runs.clear()
    return _FakeGraphDB()


def _statement_calls() -> list[tuple[str, dict]]:
    """Filter captured runs down to the ``MERGE (st:Statement ...)`` calls."""
    return [call for call in _captured_runs if "MERGE (st:Statement" in call[0]]


def _asserts_calls() -> list[tuple[str, dict]]:
    """Filter captured runs down to the legacy ``:ASSERTS`` edge writes."""
    return [call for call in _captured_runs if ":ASSERTS" in call[0]]


# ---------------------------------------------------------------------------
# Property-shape unit tests on _statement_props_from_assertion
# ---------------------------------------------------------------------------


def test_statement_props_carry_core_amr_fields():
    props = GraphDBResource._statement_props_from_assertion(
        {
            "assertion_id": "a1",
            "subject_text": "Rep. Smith",
            "predicate": "introduce",
            "object_text": "H.R. 1234",
            "amr_frame": "introduce-01",
            "amr_variable": "i",
            "amr_role_mapping": {"ARG0": "subject", "ARG1": "object"},
            "polarity": True,
            "modality": "possible",
            "is_novel_predicate": False,
            "is_atemporal": False,
            "confidence": 0.9,
        }
    )
    assert props["amr_frame"] == "introduce-01"
    assert props["amr_variable"] == "i"
    assert props["polarity"] is True
    assert props["modality"] == "possible"
    assert props["is_novel_predicate"] is False
    assert props["is_atemporal"] is False
    # role mapping is JSON-encoded
    assert json.loads(props["amr_role_mapping_json"]) == {"ARG0": "subject", "ARG1": "object"}


def test_statement_props_promote_known_qualifier_keys():
    props = GraphDBResource._statement_props_from_assertion(
        {
            "assertion_id": "a2",
            "predicate": "introduce",
            "qualifiers": {
                "time": "2025-03-15",
                "location": "Senate",
                "weather": "sunny",  # not a promoted key — JSON-only
            },
        }
    )
    assert props["qualifier_time"] == "2025-03-15"
    assert props["qualifier_location"] == "Senate"
    assert "qualifier_weather" not in props
    # Full dict still preserved as JSON blob.
    assert json.loads(props["qualifiers_json"]) == {
        "time": "2025-03-15",
        "location": "Senate",
        "weather": "sunny",
    }


def test_statement_props_drop_none_values_so_reruns_dont_unset():
    """``SET st += $props`` with ``None`` would clobber existing properties.
    The builder must drop ``None`` instead of forwarding it."""
    props = GraphDBResource._statement_props_from_assertion(
        {
            "assertion_id": "a3",
            "predicate": "x",
            "modality": None,
            "t_valid_from": None,
            "subject_entity_id": None,
        }
    )
    assert "modality" not in props
    assert "t_valid_from" not in props
    assert "subject_entity_id" not in props


def test_statement_props_carry_provenance_pointers():
    props = GraphDBResource._statement_props_from_assertion(
        {
            "assertion_id": "a4",
            "predicate": "x",
            "sentence_index": 3,
            "sentence_char_start": 100,
            "sentence_char_end": 142,
            "source_document_id": "doc-1",
            "chunk_id": "chunk-1",
            "code_location": "congress_data",
        }
    )
    assert props["sentence_index"] == 3
    assert props["sentence_char_start"] == 100
    assert props["sentence_char_end"] == 142
    assert props["source_document_id"] == "doc-1"
    assert props["chunk_id"] == "chunk-1"
    assert props["code_location"] == "congress_data"


def test_statement_props_carry_entity_and_mention_ids():
    props = GraphDBResource._statement_props_from_assertion(
        {
            "assertion_id": "a5",
            "predicate": "x",
            "subject_entity_id": "ent-subj",
            "object_entity_id": "ent-obj",
            "subject_mention_id": "men-subj",
            "object_mention_id": "men-obj",
        }
    )
    assert props["subject_entity_id"] == "ent-subj"
    assert props["object_entity_id"] == "ent-obj"
    assert props["subject_mention_id"] == "men-subj"
    assert props["object_mention_id"] == "men-obj"


def test_statement_props_fall_back_to_canonical_ids_for_entity_pointers():
    """The platinum-layer assertion_graph asset enriches the dict with
    ``subject_canonical_id`` / ``object_canonical_id`` after resolution. When
    the Assertion model's own ``subject_entity_id`` is ``None``, the writer
    should fall back to the resolved canonical id."""
    props = GraphDBResource._statement_props_from_assertion(
        {
            "assertion_id": "a6",
            "predicate": "x",
            "subject_entity_id": None,
            "object_entity_id": None,
            "subject_canonical_id": "canon-subj",
            "object_canonical_id": "canon-obj",
        }
    )
    assert props["subject_entity_id"] == "canon-subj"
    assert props["object_entity_id"] == "canon-obj"


# ---------------------------------------------------------------------------
# Integration with sync_assertions_to_neo4j — Cypher + label assertions
# ---------------------------------------------------------------------------


def test_sync_writes_statement_node_with_amr_props():
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {
                "assertion_id": "stmt-1",
                "subject_text": "Rep. Smith",
                "predicate": "introduce",
                "object_text": "H.R. 1234",
                "amr_frame": "introduce-01",
                "polarity": True,
                "is_novel_predicate": False,
                "qualifiers": {"time": "2025-03-15"},
                "confidence": 0.95,
            }
        ]
    )

    stmt_calls = _statement_calls()
    assert len(stmt_calls) == 1
    cypher, params = stmt_calls[0]
    assert params["assertion_id"] == "stmt-1"
    assert params["polarity"] is True
    assert params["is_novel"] is False
    assert params["props"]["amr_frame"] == "introduce-01"
    assert params["props"]["qualifier_time"] == "2025-03-15"
    # The MERGE keys on assertion_id so re-runs are idempotent.
    assert "MERGE (st:Statement {assertion_id: $assertion_id})" in cypher


def test_negated_polarity_triggers_negated_label():
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {
                "assertion_id": "stmt-neg",
                "predicate": "support",
                "polarity": False,
            }
        ]
    )
    cypher, params = _statement_calls()[0]
    assert params["polarity"] is False
    # The Cypher must include the conditional :Negated label clause.
    assert "SET st:Negated" in cypher


def test_novel_predicate_triggers_novel_label():
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {
                "assertion_id": "stmt-novel",
                "predicate": "frob",
                "is_novel_predicate": True,
            }
        ]
    )
    cypher, params = _statement_calls()[0]
    assert params["is_novel"] is True
    assert "SET st:NovelPredicate" in cypher


def test_qualifiers_dict_lands_on_statement():
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {
                "assertion_id": "stmt-q",
                "predicate": "vote",
                "qualifiers": {"time": "2025-03-15", "location": "Senate"},
            }
        ]
    )
    _, params = _statement_calls()[0]
    props = params["props"]
    assert props["qualifier_time"] == "2025-03-15"
    assert props["qualifier_location"] == "Senate"
    assert json.loads(props["qualifiers_json"]) == {
        "time": "2025-03-15",
        "location": "Senate",
    }


def test_is_atemporal_flag_lands_on_statement():
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {
                "assertion_id": "stmt-cite",
                "predicate": "cites",
                "is_atemporal": True,
            }
        ]
    )
    _, params = _statement_calls()[0]
    assert params["props"]["is_atemporal"] is True


def test_temporal_validity_fields_land_on_statement():
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {
                "assertion_id": "stmt-temporal",
                "predicate": "serves",
                "t_valid_from": "2025-01-03",
                "t_valid_until": "2027-01-03",
            }
        ]
    )
    _, params = _statement_calls()[0]
    assert params["props"]["t_valid_from"] == "2025-01-03"
    assert params["props"]["t_valid_until"] == "2027-01-03"


def test_missing_subject_entity_id_skips_asserts_edge_but_writes_statement():
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {
                "assertion_id": "stmt-unlinked",
                "predicate": "x",
                "subject_canonical_id": None,
                "object_canonical_id": "ent-obj",
            }
        ]
    )
    # Statement still written.
    assert len(_statement_calls()) == 1
    # But no :ASSERTS edge because subject didn't resolve.
    assert len(_asserts_calls()) == 0


def test_fully_linked_assertion_still_writes_asserts_edge():
    """The legacy ``:ASSERTS`` edge between Entity nodes is preserved for
    backward-compat with existing graph queries."""
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {
                "assertion_id": "stmt-linked",
                "predicate": "sponsors",
                "subject_canonical_id": "ent-subj",
                "object_canonical_id": "ent-obj",
                "confidence": 0.9,
            }
        ]
    )
    assert len(_statement_calls()) == 1
    edge_calls = _asserts_calls()
    assert len(edge_calls) == 1
    _, params = edge_calls[0]
    assert params["subj_id"] == "ent-subj"
    assert params["obj_id"] == "ent-obj"
    assert params["assertion_id"] == "stmt-linked"


def test_merge_is_idempotent_on_assertion_id():
    """Re-running the writer with the same assertion_id must not duplicate
    Statement nodes — that's the whole point of keying on assertion_id."""
    resource = _make_resource()
    resource.sync_assertions_to_neo4j(
        [
            {"assertion_id": "stmt-dup", "predicate": "x"},
            {"assertion_id": "stmt-dup", "predicate": "x"},
        ]
    )
    stmt_calls = _statement_calls()
    assert len(stmt_calls) == 2  # two calls
    # Both use the same key.
    assert {c[1]["assertion_id"] for c in stmt_calls} == {"stmt-dup"}
    # Both use the MERGE-by-assertion_id pattern.
    for cypher, _ in stmt_calls:
        assert "MERGE (st:Statement {assertion_id: $assertion_id})" in cypher


def test_empty_input_returns_zero_without_driver_init():
    """Empty input must short-circuit before opening a Neo4j connection."""
    resource = _make_resource()
    assert resource.sync_assertions_to_neo4j([]) == 0
    assert _captured_runs == []


# ---------------------------------------------------------------------------
# Round-trip through the real Pydantic model (not a hand-rolled dict)
# ---------------------------------------------------------------------------


def test_real_assertion_model_dump_round_trips_through_writer():
    """Sanity check: feeding ``Assertion.model_dump()`` through the writer
    produces a Statement with all the AMR fields we expect. This catches
    field-name drift between the contract and the writer."""
    try:
        from catalyst_contracts_core import Assertion, ExtractionMethod, Provenance
    except ModuleNotFoundError:
        pytest.skip("catalyst_contracts_core not installed in this environment")

    a = Assertion(
        assertion_id="real-1",
        subject_text="Rep. Smith",
        predicate="introduce",
        object_text="H.R. 1234",
        amr_frame="introduce-01",
        amr_variable="i",
        amr_role_mapping={"ARG0": "subject", "ARG1": "object"},
        polarity=True,
        modality=None,
        is_novel_predicate=False,
        qualifiers={"time": "2025-03-15"},
        confidence=0.92,
        provenance=Provenance(
            source_document_id="doc-1",
            chunk_id="chunk-1",
            extraction_method=ExtractionMethod.AMR_PROJECTION,
        ),
    )

    resource = _make_resource()
    record = a.model_dump()
    # The asset enriches the dict with these flat top-level fields — mirror
    # that here so the writer sees the same shape it sees in prod.
    record["source_document_id"] = a.provenance.source_document_id
    record["chunk_id"] = a.provenance.chunk_id
    record["code_location"] = a.provenance.code_location

    resource.sync_assertions_to_neo4j([record])

    _, params = _statement_calls()[0]
    props = params["props"]
    assert props["amr_frame"] == "introduce-01"
    assert props["polarity"] is True
    assert props["qualifier_time"] == "2025-03-15"
    assert json.loads(props["amr_role_mapping_json"]) == {
        "ARG0": "subject",
        "ARG1": "object",
    }
