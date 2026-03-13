"""Unit tests for open-leaks bronze extraction parsers."""

from open_leaks.assets.extraction import _extract_subject, _extract_tags, _parse_cable_block
from open_leaks.config import OpenLeaksConfig


def test_parse_cable_block():
    """Test parsing a single cable CSV block."""
    lines = [
        '"1","1/1/2005 12:00","05PARIS001","Embassy Paris","CONFIDENTIAL","","HEADER TEXT","SUBJECT: Test cable subject\\nTAGS: PREL, PGOV, FR\\nBody text here."\n'
    ]
    result = _parse_cable_block(lines)
    assert result is not None
    assert result["id"] == "1"
    assert result["origin"] == "Embassy Paris"
    assert result["classification"] == "CONFIDENTIAL"


def test_extract_subject():
    """Test SUBJECT: extraction from cable body."""
    body = "HEADER LINE\nSUBJECT: Arms control negotiations\nTAGS: PREL\nBody text."
    assert _extract_subject(body) == "Arms control negotiations"


def test_extract_subject_subj():
    """Test SUBJ: variant extraction."""
    body = "SUBJ: Trade deal update\nBody text."
    assert _extract_subject(body) == "Trade deal update"


def test_extract_subject_missing():
    """Test fallback when no subject line."""
    assert _extract_subject("No subject here\nJust body text") == ""


def test_extract_tags():
    """Test TAGS: extraction from cable body."""
    body = "SUBJECT: Test\nTAGS: PREL, PGOV, FR, ECON\nBody text."
    tags = _extract_tags(body)
    assert tags == ["PREL", "PGOV", "FR", "ECON"]


def test_extract_tags_missing():
    """Test fallback when no tags line."""
    assert _extract_tags("No tags here") == []


def test_config_defaults():
    """Test OpenLeaksConfig instantiation with defaults."""
    config = OpenLeaksConfig()
    assert config.batch_size == 100
    assert config.max_cables == 0
    assert "icij.org" in config.icij_bulk_url
