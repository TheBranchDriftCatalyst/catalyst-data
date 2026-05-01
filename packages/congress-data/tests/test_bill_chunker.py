"""Unit tests for bill_chunker XML parsing.

Covers the XML-smart path (preferred) which dispatches by root document
shape — bill XMLs use ``<legis-body>``, House/Senate resolution XMLs use
``<resolution-body>``. Both feed the same downstream section/title
chunking logic.
"""

from __future__ import annotations

from congress_data.bill_chunker import _parse_xml

# Minimal fixtures — we only need enough structure for _parse_xml to find
# the body element and walk its children. Section content is intentionally
# tiny so we can assert on chunk count rather than character math.

BILL_XML = """<?xml version="1.0"?>
<bill>
  <metadata />
  <form>Bill preamble text.</form>
  <legis-body>
    <section><enum>1.</enum><header>Short title</header><text>This Act may be cited.</text></section>
    <section><enum>2.</enum><header>Findings</header><text>Congress finds the following.</text></section>
  </legis-body>
</bill>
"""

RESOLUTION_XML = """<?xml version="1.0"?>
<resolution>
  <metadata />
  <form>Resolution preamble.</form>
  <resolution-body>
    <section><enum>1.</enum><header>Title</header><text>Resolution title.</text></section>
    <section><enum>2.</enum><header>Body</header><text>Resolution body.</text></section>
  </resolution-body>
</resolution>
"""


def test_parse_xml_bill_with_legis_body():
    preamble, candidates = _parse_xml(BILL_XML)
    assert preamble and "Bill preamble" in preamble
    assert len(candidates) == 2


def test_parse_xml_resolution_with_resolution_body():
    """Regression: house/senate resolutions use <resolution-body>, not <legis-body>.

    Before this fix, the parser raised ValueError and the chunker fell back
    to regex mode, producing a single coarse chunk for the whole document
    instead of one chunk per section.
    """
    preamble, candidates = _parse_xml(RESOLUTION_XML)
    assert preamble and "Resolution preamble" in preamble
    assert len(candidates) == 2


def test_parse_xml_missing_body_raises():
    bad = "<root><metadata/></root>"
    try:
        _parse_xml(bad)
    except ValueError as e:
        assert "legis-body" in str(e) or "resolution-body" in str(e)
    else:
        raise AssertionError("expected ValueError for missing body element")
