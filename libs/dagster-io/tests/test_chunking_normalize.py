"""Tests for Unicode normalization at chunking (ingestion) layer."""

from __future__ import annotations

from dagster_io.chunking import ChunkingResource
from dagster_io.text import normalize_text


def test_normalize_text_fullwidth():
    """Fullwidth chars converted to ASCII."""
    assert normalize_text("Rick Spence： CIA") == "Rick Spence: CIA"
    assert normalize_text("Piers Morgan ｜ Full") == "Piers Morgan | Full"


def test_normalize_text_ligatures():
    assert normalize_text("ﬁnance ﬂow") == "finance flow"


def test_normalize_text_passthrough():
    """Normal ASCII passes through unchanged."""
    text = "Joe Biden spoke at the UN."
    assert normalize_text(text) == text


def test_normalize_text_null_bytes():
    assert normalize_text("hello\x00world") == "helloworld"


def test_chunking_normalizes_title():
    """ChunkingResource normalizes title before prepending."""
    res = ChunkingResource(chunk_size=500, chunk_overlap=50, prepend_title=True)
    chunks = res.chunk_document(
        "doc-1",
        "Rick Spence： CIA Connections ｜ Full Interview",
        "Normal content here that is long enough to produce at least one chunk for testing purposes.",
    )
    assert len(chunks) >= 1
    assert "：" not in chunks[0].text
    assert ":" in chunks[0].text
    assert "｜" not in chunks[0].text
    assert "|" in chunks[0].text


def test_chunking_normalizes_content():
    """ChunkingResource normalizes content fullwidth chars."""
    res = ChunkingResource(chunk_size=500, chunk_overlap=50, prepend_title=False)
    chunks = res.chunk_document(
        "doc-1",
        "",
        "The report stated： ﬁnancial ﬂows were disrupted｜severely. " * 5,
    )
    assert len(chunks) >= 1
    assert "：" not in chunks[0].text
    assert "｜" not in chunks[0].text
    assert "fi" in chunks[0].text  # ligature decomposed


def test_passthrough_normalizes():
    """Passthrough chunks also get normalized."""
    res = ChunkingResource(prepend_title=True)
    chunks = res.passthrough(
        "doc-1",
        "Title：With Fullwidth",
        "Content with ﬁligree ﬂags",
    )
    assert len(chunks) == 1
    assert "：" not in chunks[0].text
    assert ":" in chunks[0].text
    assert "fi" in chunks[0].text


def test_empty_content_safe():
    """Empty/whitespace content returns empty list."""
    res = ChunkingResource()
    assert res.chunk_document("d", "t", "") == []
    assert res.chunk_document("d", "t", "   ") == []
    assert res.passthrough("d", "t", "") == []
