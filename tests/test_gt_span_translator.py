"""Tests for span translator helpers in tests/shared/gt_translation.py.

Covers:
- chunk_to_doc: forward translation
- doc_to_chunk: reverse translation
- Round-trip identity: chunk → doc → chunk == identity
- Error cases: missing offset, negative spans
"""

from __future__ import annotations

import pytest

from tests.shared.gt_translation import chunk_to_doc, doc_to_chunk

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _meta(offset: int | None) -> dict:
    """Build a minimal chunk metadata dict."""
    return {"chunk_char_offset": offset, "chunk_size": 1000}


# ---------------------------------------------------------------------------
# chunk_to_doc
# ---------------------------------------------------------------------------


class TestChunkToDoc:
    def test_basic_forward(self):
        meta = _meta(100)
        doc_start, doc_end = chunk_to_doc(meta, 10, 25)
        assert doc_start == 110
        assert doc_end == 125

    def test_zero_offset(self):
        meta = _meta(0)
        doc_start, doc_end = chunk_to_doc(meta, 5, 15)
        assert doc_start == 5
        assert doc_end == 15

    def test_large_offset(self):
        meta = _meta(99999)
        doc_start, doc_end = chunk_to_doc(meta, 0, 100)
        assert doc_start == 99999
        assert doc_end == 100099

    def test_span_at_start_of_chunk(self):
        meta = _meta(500)
        doc_start, doc_end = chunk_to_doc(meta, 0, 10)
        assert doc_start == 500
        assert doc_end == 510

    def test_missing_offset_raises(self):
        with pytest.raises(ValueError, match="chunk_char_offset is None"):
            chunk_to_doc(_meta(None), 0, 5)

    def test_missing_key_raises(self):
        """If the key is entirely absent it behaves like None."""
        with pytest.raises(ValueError, match="chunk_char_offset is None"):
            chunk_to_doc({}, 0, 5)


# ---------------------------------------------------------------------------
# doc_to_chunk
# ---------------------------------------------------------------------------


class TestDocToChunk:
    def test_basic_reverse(self):
        meta = _meta(100)
        chunk_start, chunk_end = doc_to_chunk(meta, 110, 125)
        assert chunk_start == 10
        assert chunk_end == 25

    def test_zero_offset(self):
        meta = _meta(0)
        chunk_start, chunk_end = doc_to_chunk(meta, 5, 15)
        assert chunk_start == 5
        assert chunk_end == 15

    def test_span_at_doc_origin(self):
        meta = _meta(300)
        chunk_start, chunk_end = doc_to_chunk(meta, 300, 350)
        assert chunk_start == 0
        assert chunk_end == 50

    def test_missing_offset_raises(self):
        with pytest.raises(ValueError, match="chunk_char_offset is None"):
            doc_to_chunk(_meta(None), 100, 200)

    def test_span_before_chunk_raises(self):
        """doc span that starts before chunk offset produces negative chunk span."""
        meta = _meta(100)
        with pytest.raises(ValueError, match="Invalid chunk-frame span"):
            doc_to_chunk(meta, 50, 80)


# ---------------------------------------------------------------------------
# Round-trip property
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """chunk → doc → chunk must be the identity."""

    @pytest.mark.parametrize(
        "offset, span_start, span_end",
        [
            (0, 0, 50),
            (0, 10, 30),
            (100, 0, 200),
            (100, 50, 150),
            (99999, 1000, 2000),
            (0, 0, 1),  # minimal span
            (500, 0, 500),  # chunk-length span
        ],
    )
    def test_roundtrip_chunk_doc_chunk(self, offset: int, span_start: int, span_end: int):
        meta = _meta(offset)
        doc_start, doc_end = chunk_to_doc(meta, span_start, span_end)
        back_start, back_end = doc_to_chunk(meta, doc_start, doc_end)
        assert back_start == span_start, f"span_start mismatch at offset={offset}"
        assert back_end == span_end, f"span_end mismatch at offset={offset}"

    @pytest.mark.parametrize(
        "offset, doc_start, doc_end",
        [
            (0, 0, 100),
            (50, 50, 200),
            (1000, 1000, 1500),
        ],
    )
    def test_roundtrip_doc_chunk_doc(self, offset: int, doc_start: int, doc_end: int):
        """doc → chunk → doc must also be identity."""
        meta = _meta(offset)
        chunk_start, chunk_end = doc_to_chunk(meta, doc_start, doc_end)
        back_doc_start, back_doc_end = chunk_to_doc(meta, chunk_start, chunk_end)
        assert back_doc_start == doc_start
        assert back_doc_end == doc_end
