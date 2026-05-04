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


# ── CD-lxcf: byte_fallback offenders that NFKC alone leaves untouched ──


def test_normalize_text_smart_quotes():
    """Smart quotes → ASCII quotes (NFKC leaves them alone)."""
    assert normalize_text("“Hello”") == '"Hello"'
    assert normalize_text("it’s") == "it's"
    assert normalize_text("‘single’") == "'single'"


def test_normalize_text_dashes():
    """Em/en dashes + minus sign → ASCII hyphen."""
    assert normalize_text("range 1–2") == "range 1-2"  # en dash
    assert normalize_text("a—b") == "a-b"  # em dash
    assert normalize_text("temp −5°C") == "temp -5°C"  # minus + degree (NFKC keeps degree)


def test_normalize_text_nbsp_and_zero_width():
    """Non-breaking space → regular space; zero-width chars dropped."""
    assert normalize_text("foo bar") == "foo bar"  # NBSP
    assert normalize_text("foo​bar") == "foobar"  # ZERO WIDTH SPACE
    assert normalize_text("﻿hello") == "hello"  # BOM
    assert normalize_text("soft­hyphen") == "softhyphen"


def test_normalize_text_ellipsis_and_bullets():
    assert normalize_text("wait…") == "wait..."
    assert normalize_text("• item") == "* item"


def test_normalize_text_idempotent():
    """normalize_text(normalize_text(s)) == normalize_text(s)."""
    inputs = [
        "“mixed” —  test…",
        "plain ascii",
        "Café résumé",  # diacritics preserved
    ]
    for s in inputs:
        once = normalize_text(s)
        assert normalize_text(once) == once


def test_normalize_text_preserves_diacritics():
    """é, ñ, ü etc. stay — modern tokenizers handle them."""
    s = "Café résumé piñata Über"
    assert normalize_text(s) == s


def test_chunk_text_helper_normalizes():
    """Standalone chunk_text() normalizes input (CD-lxcf)."""
    from dagster_io.chunking import chunk_text

    raw = "“Quoted” text — with em-dash. " * 30
    out = chunk_text(raw, chunk_size=200, chunk_overlap=20)
    joined = " ".join(out)
    assert "“" not in joined
    assert "”" not in joined
    assert "—" not in joined
    assert '"' in joined
    assert "-" in joined


def test_chunk_document_helper_normalizes_title_and_content():
    """Standalone chunk_document() normalizes title + content."""
    from dagster_io.chunking import chunk_document

    chunks = chunk_document(
        document_id="d",
        title="“Title” — fancy",
        content="Body text with ‘smart’ quotes. " * 30,
        chunk_size=200,
        chunk_overlap=20,
    )
    assert chunks
    text0 = chunks[0].text
    assert "“" not in text0
    assert "‘" not in text0
    assert '"Title" - fancy' in text0


def test_chunk_multi_speaker_segments_normalizes():
    """Segment text gets normalized before windowing."""
    res = ChunkingResource(chunk_size=200, chunk_overlap=20)
    segments = [
        {"text": "“Hello”, said Alice.", "speaker": "S1", "start": 0, "end": 1, "words": []},
        {"text": "—And then Bob replied.", "speaker": "S2", "start": 1, "end": 2, "words": []},
    ]
    chunks = res.chunk_multi_speaker_segments(segments, document_id="d", title="t")
    assert chunks
    joined = " ".join(c.text for c in chunks)
    assert "“" not in joined
    assert "—" not in joined
