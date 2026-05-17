"""Tests for the RDF/RDF-star bridge over Neosemantics (n10s).

These tests don't require a running Neo4j. They exercise the row-to-Turtle
serializer and the ensure_n10s_initialized guard. End-to-end round-trip
against a real Neo4j with n10s is covered by an integration test marked
``@pytest.mark.integration`` which the dev compose stack satisfies (see
docker-compose.dev.yml).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from knowledge_graph.rdf_bridge import (
    _escape_literal,
    _rows_to_turtle_star,
    ensure_n10s_initialized,
)


def test_escape_literal_handles_quotes_backslash_newlines():
    assert _escape_literal('a "b" c') == 'a \\"b\\" c'
    assert _escape_literal("line1\nline2") == "line1\\nline2"
    assert _escape_literal("path\\to") == "path\\\\to"


def test_rows_to_turtle_star_emits_iri_for_non_literal():
    rows = [
        {
            "subject": "urn:catalyst:smith-bioguide",
            "predicate": "urn:catalyst:sponsored",
            "object": "urn:catalyst:hr-1234-119",
            "isLiteral": False,
            "literalType": None,
            "literalLang": None,
        }
    ]
    out = _rows_to_turtle_star(rows)
    assert "<urn:catalyst:smith-bioguide> <urn:catalyst:sponsored> <urn:catalyst:hr-1234-119> ." in out


def test_rows_to_turtle_star_typed_literal():
    rows = [
        {
            "subject": "urn:catalyst:stmt-001",
            "predicate": "urn:catalyst:t_valid_from",
            "object": "2025-03-15",
            "isLiteral": True,
            "literalType": "http://www.w3.org/2001/XMLSchema#date",
            "literalLang": None,
        }
    ]
    out = _rows_to_turtle_star(rows)
    assert '"2025-03-15"^^<http://www.w3.org/2001/XMLSchema#date>' in out


def test_rows_to_turtle_star_language_tagged_literal():
    rows = [
        {
            "subject": "urn:catalyst:committee-1",
            "predicate": "urn:catalyst:name",
            "object": "Energy and Commerce",
            "isLiteral": True,
            "literalType": None,
            "literalLang": "en",
        }
    ]
    out = _rows_to_turtle_star(rows)
    assert '"Energy and Commerce"@en' in out


def test_rows_to_turtle_star_plain_literal():
    rows = [
        {
            "subject": "urn:catalyst:stmt-001",
            "predicate": "urn:catalyst:canonical_predicate",
            "object": "sponsored",
            "isLiteral": True,
            "literalType": None,
            "literalLang": None,
        }
    ]
    out = _rows_to_turtle_star(rows)
    assert '"sponsored"' in out
    assert "^^" not in out  # no datatype suffix
    assert "@en" not in out  # no language tag


def test_rows_to_turtle_star_round_trips_through_rdflib():
    """The output Turtle must parse cleanly with rdflib."""
    import rdflib  # noqa: PLC0415

    rows = [
        {
            "subject": "urn:catalyst:smith-bioguide",
            "predicate": "urn:catalyst:sponsored",
            "object": "urn:catalyst:hr-1234-119",
            "isLiteral": False,
            "literalType": None,
            "literalLang": None,
        },
        {
            "subject": "urn:catalyst:stmt-001",
            "predicate": "urn:catalyst:t_valid_from",
            "object": "2025-03-15",
            "isLiteral": True,
            "literalType": "http://www.w3.org/2001/XMLSchema#date",
            "literalLang": None,
        },
    ]
    out = _rows_to_turtle_star(rows)
    graph = rdflib.Graph()
    graph.parse(data=out, format="ttl")
    # Two statements emitted, two triples parsed.
    assert len(graph) == 2
    # The date literal lands typed
    date_triples = list(
        graph.triples(
            (
                rdflib.URIRef("urn:catalyst:stmt-001"),
                rdflib.URIRef("urn:catalyst:t_valid_from"),
                None,
            )
        )
    )
    assert len(date_triples) == 1
    assert date_triples[0][2].datatype == rdflib.URIRef("http://www.w3.org/2001/XMLSchema#date")


def test_ensure_n10s_initialized_returns_true_when_show_succeeds():
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    driver.session.return_value.__exit__.return_value = False
    session.run.return_value.consume.return_value = None

    assert ensure_n10s_initialized(driver) is True
    session.run.assert_called_once_with("CALL n10s.graphconfig.show()")


def test_ensure_n10s_initialized_returns_false_on_error():
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    driver.session.return_value.__exit__.return_value = False
    session.run.side_effect = RuntimeError("n10s not loaded")

    assert ensure_n10s_initialized(driver) is False


@pytest.mark.integration
def test_round_trip_against_live_neo4j():
    """End-to-end smoke against a live Neo4j with n10s loaded.

    Requires the docker-compose.dev.yml stack to be up:
        docker compose -f docker-compose.dev.yml up -d neo4j neo4j-n10s-init

    Skipped automatically in CI unless RUN_NEO4J_INTEGRATION=1.
    """
    import os  # noqa: PLC0415

    if not os.environ.get("RUN_NEO4J_INTEGRATION"):
        pytest.skip("set RUN_NEO4J_INTEGRATION=1 to run this against live Neo4j")

    from knowledge_graph.rdf_bridge import (  # noqa: PLC0415
        ensure_n10s_initialized,
        round_trip_subgraph,
    )
    from neo4j import GraphDatabase  # noqa: PLC0415

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    driver = GraphDatabase.driver(uri, auth=("neo4j", "neo4j-homelab"))
    try:
        assert ensure_n10s_initialized(driver), "n10s init Job hasn't run"

        with driver.session() as session:
            session.run("MATCH (n:RdfBridgeTest) DETACH DELETE n").consume()
            session.run(
                "CREATE (s:Statement:RdfBridgeTest {uri: 'urn:catalyst:test-stmt-1', "
                "canonical_predicate: 'sponsored', polarity: true})"
            ).consume()

        graph = round_trip_subgraph(
            driver,
            "MATCH (s:RdfBridgeTest) RETURN s",
            {},
        )
        assert len(graph) >= 1
    finally:
        with driver.session() as session:
            session.run("MATCH (n:RdfBridgeTest) DETACH DELETE n").consume()
        driver.close()
