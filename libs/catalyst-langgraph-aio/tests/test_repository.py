"""Tests for JsonlRepository."""

import json
from pathlib import Path

import pytest

from catalyst_langgraph.repository.jsonl import JsonlRepository


@pytest.mark.asyncio
async def test_save_mentions(tmp_path: Path):
    repo = JsonlRepository(tmp_path)
    mentions = [
        {"surface_form": "Acme Corp", "entity_type": "ORG"},
        {"surface_form": "John Smith", "entity_type": "PERSON"},
    ]
    await repo.save_mentions("doc-001", mentions)

    output_file = tmp_path / "doc-001" / "mentions.jsonl"
    assert output_file.exists()

    lines = output_file.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["surface_form"] == "Acme Corp"
    assert json.loads(lines[1])["entity_type"] == "PERSON"


@pytest.mark.asyncio
async def test_save_propositions(tmp_path: Path):
    repo = JsonlRepository(tmp_path)
    props = [{"subject": "John", "predicate": "works_for", "object": "Acme"}]
    await repo.save_propositions("doc-002", props)

    output_file = tmp_path / "doc-002" / "propositions.jsonl"
    assert output_file.exists()
    lines = output_file.read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["predicate"] == "works_for"


@pytest.mark.asyncio
async def test_save_audit_trail(tmp_path: Path):
    repo = JsonlRepository(tmp_path)
    events = [{"node_name": "extract_mentions", "status": "completed"}]
    await repo.save_audit_trail("doc-003", events)

    output_file = tmp_path / "doc-003" / "audit_trail.jsonl"
    assert output_file.exists()


@pytest.mark.asyncio
async def test_append_mode(tmp_path: Path):
    repo = JsonlRepository(tmp_path)
    await repo.save_mentions("doc-004", [{"a": 1}])
    await repo.save_mentions("doc-004", [{"b": 2}])

    output_file = tmp_path / "doc-004" / "mentions.jsonl"
    lines = output_file.read_text().strip().split("\n")
    assert len(lines) == 2


# --- A8: document_id path traversal tests ---


class TestDocumentIdValidation:
    """Ensure document_id is sanitized to prevent path traversal."""

    @pytest.mark.asyncio
    async def test_valid_simple_id(self, tmp_path: Path):
        repo = JsonlRepository(tmp_path)
        await repo.save_mentions("doc-001", [{"a": 1}])
        assert (tmp_path / "doc-001" / "mentions.jsonl").exists()

    @pytest.mark.asyncio
    async def test_valid_id_with_dots_and_underscores(self, tmp_path: Path):
        repo = JsonlRepository(tmp_path)
        await repo.save_mentions("doc_v1.2.3", [{"a": 1}])
        assert (tmp_path / "doc_v1.2.3" / "mentions.jsonl").exists()

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_dotdot(self, tmp_path: Path):
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("../../etc/passwd", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_rejects_forward_slash(self, tmp_path: Path):
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("foo/bar", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_rejects_backslash(self, tmp_path: Path):
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("foo\\bar", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_rejects_empty_string(self, tmp_path: Path):
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_rejects_dotdot_only(self, tmp_path: Path):
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("..", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_rejects_ssh_traversal(self, tmp_path: Path):
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("../../.ssh/authorized_keys", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_rejects_leading_dot(self, tmp_path: Path):
        """IDs starting with a dot are rejected (prevents hidden files/dirs)."""
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions(".hidden", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_rejects_dotdot_prefix(self, tmp_path: Path):
        """IDs starting with .. are rejected."""
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("..hidden", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_valid_id_with_dots_in_middle(self, tmp_path: Path):
        """Dots in the middle of an ID are fine (e.g. versioned docs)."""
        repo = JsonlRepository(tmp_path)
        await repo.save_mentions("doc.with.dots", [{"a": 1}])
        assert (tmp_path / "doc.with.dots" / "mentions.jsonl").exists()

    @pytest.mark.asyncio
    async def test_rejects_nested_path_forward_slash(self, tmp_path: Path):
        """No nested directory paths allowed."""
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("doc/subdir", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_valid_alphanumeric_hyphens_underscores(self, tmp_path: Path):
        """Standard alphanumeric IDs with hyphens and underscores work."""
        repo = JsonlRepository(tmp_path)
        await repo.save_mentions("valid-doc-123", [{"a": 1}])
        assert (tmp_path / "valid-doc-123" / "mentions.jsonl").exists()

    @pytest.mark.asyncio
    async def test_rejects_unicode_characters(self, tmp_path: Path):
        """Unicode characters are rejected by the regex (only ASCII alphanumeric allowed).

        Decision: document_ids must be ASCII-safe for filesystem portability.
        """
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("\u65e5\u672c\u8a9edoc", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_validation_before_filesystem_ops(self, tmp_path: Path):
        """Invalid IDs must not create any directories or files."""
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError):
            await repo.save_mentions("../../etc/passwd", [{"a": 1}])
        # No directory should have been created
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_rejects_spaces(self, tmp_path: Path):
        """Spaces are not allowed in document_ids."""
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("doc with spaces", [{"a": 1}])

    @pytest.mark.asyncio
    async def test_rejects_leading_hyphen(self, tmp_path: Path):
        """IDs starting with a hyphen are rejected (must start with alphanumeric)."""
        repo = JsonlRepository(tmp_path)
        with pytest.raises(ValueError, match="Invalid document_id"):
            await repo.save_mentions("-leading-hyphen", [{"a": 1}])
