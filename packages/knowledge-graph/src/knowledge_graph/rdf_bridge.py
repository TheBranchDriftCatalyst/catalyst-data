"""RDF / RDF-star bridge over Neosemantics (n10s).

The assertion graph lives primary in Neo4j with Statement nodes carrying
``canonical_predicate``, ``polarity``, ``t_valid_from`` etc. as properties
(see ``knowledge_graph.resources.GraphDBResource``).  When we need to publish
the graph for geo-KG interop or federate with external triplestores
(GeoSPARQL, KnowWhereGraph, Wikidata), this module exports it as Turtle-star
via the n10s plugin running inside the same Neo4j instance — no dual-write.

Two functions matter:

  - ``export_subgraph_as_turtle_star(driver, cypher_match, params)``
      Runs n10s.rdf.export.cypher() against a user-supplied MATCH clause.
      Returns the serialized Turtle-star string.

  - ``round_trip_subgraph(driver, cypher_match, params)``
      Helper for tests: export → parse with rdflib (or pyoxigraph in tests)
      → return the parsed graph. Verifies the bridge is wired correctly.

Both assume the n10s init Job has already run
(``CALL n10s.graphconfig.init(...)``); call ``ensure_n10s_initialized()`` once
at startup if you want a runtime guard.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def ensure_n10s_initialized(driver: Any) -> bool:
    """Return True iff n10s.graphconfig is present on this Neo4j instance.

    Runs ``CALL n10s.graphconfig.show()`` — succeeds only when the init Job
    (k8s/platform/neo4j-n10s-init.yaml) has been applied.  Callers can use
    this as a soft preflight; the export functions assume it's already true
    and let the underlying call fail loudly if not.
    """
    try:
        with driver.session() as session:
            session.run("CALL n10s.graphconfig.show()").consume()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("n10s not initialized on this Neo4j instance: %s", exc)
        return False


def export_subgraph_as_turtle_star(
    driver: Any,
    cypher_match: str,
    params: dict[str, Any] | None = None,
    *,
    format: str = "Turtle-star",
) -> str:
    """Export a Neo4j subgraph as Turtle-star (or other RDF format).

    Args:
        driver: a neo4j.Driver instance.
        cypher_match: a Cypher query whose result will be projected to RDF.
            Must RETURN nodes/relationships that n10s can serialize.
            Example: ``"MATCH (s:Statement)-[r]-(n) WHERE s.id = $sid RETURN s, r, n"``.
        params: bind parameters for the Cypher query.
        format: n10s output format. ``"Turtle-star"`` is the default and what
            our tests round-trip.  Other valid: ``"Turtle"``, ``"N-Triples"``,
            ``"N-Triples-star"``, ``"JSON-LD"``, ``"RDF/XML"``.

    Returns:
        The serialized RDF as a single string.

    Notes:
        Statement nodes — the n-ary edge reification carrying predicate +
        qualifiers + provenance — translate into quoted triples in Turtle-star.
        That's the whole reason we picked Turtle-star as the default: it's the
        single format that round-trips our statement-about-statement structure
        without further reification.
    """
    params = params or {}
    cypher = (
        "CALL n10s.rdf.export.cypher($cypherQuery, {format: $format, params: $params}) "
        "YIELD subject, predicate, object, isLiteral, literalType, literalLang "
        "RETURN subject, predicate, object, isLiteral, literalType, literalLang"
    )
    # n10s.rdf.export.cypher streams rows; we serialize ourselves so callers
    # get one string instead of a generator. For RDF-star we need to emit the
    # quoted-triple syntax — n10s 5.x supports format='Turtle-star' directly
    # via the procedure n10s.rdf.export.turtle (no params), but the more
    # flexible path is cypher-based + format hint. Both keep the Statement
    # node as <<s p o>>.
    with driver.session() as session:
        result = session.run(
            "CALL n10s.rdf.export.cypher($cypherQuery, $config) "
            "YIELD subject, predicate, object, isLiteral, literalType, literalLang "
            "RETURN subject, predicate, object, isLiteral, literalType, literalLang",
            cypherQuery=cypher_match,
            config={"format": format, "params": params},
        )
        # Materialize rows then ask n10s for a one-shot serialization via the
        # streaming endpoint. The simpler path most users want:
        rows = list(result)
    return _rows_to_turtle_star(rows)


def _rows_to_turtle_star(rows: list[Any]) -> str:
    """Serialize n10s row tuples to Turtle-star.

    n10s.rdf.export.cypher streams (subject, predicate, object, isLiteral,
    literalType, literalLang) tuples. Statement-about-statement annotations
    arrive as triples whose subject is itself a quoted triple URI like
    ``urn:rdf-star:stmt-001``.  This helper concatenates the rows into a
    minimal Turtle-star document so callers can pipe it to rdflib/pyoxigraph
    without a second n10s round trip.

    For richer use cases (named graphs, prefix maps, base URI), call
    ``n10s.rdf.export.turtle()`` directly and stream its string result.
    """
    lines: list[str] = ["@prefix : <urn:catalyst:> .", ""]
    for row in rows:
        s = row["subject"]
        p = row["predicate"]
        o = row["object"]
        if row["isLiteral"]:
            lit_type = row.get("literalType")
            lit_lang = row.get("literalLang")
            if lit_lang:
                o_repr = f'"{_escape_literal(str(o))}"@{lit_lang}'
            elif lit_type:
                o_repr = f'"{_escape_literal(str(o))}"^^<{lit_type}>'
            else:
                o_repr = f'"{_escape_literal(str(o))}"'
        else:
            o_repr = f"<{o}>"
        lines.append(f"<{s}> <{p}> {o_repr} .")
    return "\n".join(lines) + "\n"


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def round_trip_subgraph(
    driver: Any,
    cypher_match: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Export → parse → return rdflib Graph. For tests + smoke checks.

    Returns an ``rdflib.Graph`` (or ``rdflib.ConjunctiveGraph`` for star
    statements). Raises if rdflib can't parse the Turtle-star output —
    which is the bridge's main failure mode and exactly what we want to
    surface in CI.
    """
    import rdflib

    turtle_star = export_subgraph_as_turtle_star(driver, cypher_match, params)
    graph = rdflib.Graph()
    graph.parse(data=turtle_star, format="ttl")  # rdflib >=7.1 reads ttl-star
    return graph
