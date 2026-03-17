"""Tests for prompt loading utilities."""

from __future__ import annotations

from pathlib import Path

from catalyst_langgraph.prompts import ParsedPrompt, load_prompt, parse_prompt_file


class TestParsedPrompt:
    def test_defaults(self):
        p = ParsedPrompt(prompt_id="test", system_content="hello")
        assert p.model == "gpt-4o-mini"
        assert p.temperature == 0.0
        assert p.max_tokens == 4096
        assert p.metadata == {}


class TestParsePromptFile:
    def test_with_frontmatter(self, tmp_path):
        f = tmp_path / "test.prompt"
        f.write_text("---\nmodel: gpt-4o\ntemperature: 0.5\nmax_tokens: 2048\nmetadata:\n  task: ner\n---\nExtract entities.")
        result = parse_prompt_file(f)
        assert result.prompt_id == "test"
        assert result.system_content == "Extract entities."
        assert result.model == "gpt-4o"
        assert result.temperature == 0.5
        assert result.max_tokens == 2048
        assert result.metadata == {"task": "ner"}

    def test_without_frontmatter(self, tmp_path):
        f = tmp_path / "plain.prompt"
        f.write_text("Just a plain prompt body.")
        result = parse_prompt_file(f)
        assert result.system_content == "Just a plain prompt body."
        assert result.model == "gpt-4o-mini"

    def test_custom_prompt_id(self, tmp_path):
        f = tmp_path / "file.prompt"
        f.write_text("body")
        result = parse_prompt_file(f, prompt_id="custom/id")
        assert result.prompt_id == "custom/id"

    def test_empty_frontmatter(self, tmp_path):
        f = tmp_path / "empty.prompt"
        f.write_text("---\n---\nBody after empty frontmatter.")
        result = parse_prompt_file(f)
        assert result.system_content == "Body after empty frontmatter."
        assert result.model == "gpt-4o-mini"


class TestLoadPrompt:
    def test_fallback_when_no_env_var(self, monkeypatch):
        monkeypatch.delenv("PROMPT_REGISTRY_DIR", raising=False)
        result = load_prompt("test_id", "fallback text")
        assert result == "fallback text"

    def test_fallback_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMPT_REGISTRY_DIR", str(tmp_path))
        result = load_prompt("nonexistent", "fallback text")
        assert result == "fallback text"

    def test_loads_file_when_found(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMPT_REGISTRY_DIR", str(tmp_path))
        prompt_file = tmp_path / "my_prompt.prompt"
        prompt_file.write_text("---\nmodel: gpt-4o\n---\nLoaded prompt body.")
        result = load_prompt("my_prompt", "fallback")
        assert result == "Loaded prompt body."


class TestParsePromptFilePartialFrontmatter:
    def test_single_triple_dash_no_closing(self, tmp_path):
        """Cover lines 42-43: starts with --- but no closing --- delimiter."""
        f = tmp_path / "partial.prompt"
        f.write_text("---\nThis has no closing frontmatter delimiter")
        result = parse_prompt_file(f)
        assert result.system_content == "---\nThis has no closing frontmatter delimiter"
        assert result.model == "gpt-4o-mini"
