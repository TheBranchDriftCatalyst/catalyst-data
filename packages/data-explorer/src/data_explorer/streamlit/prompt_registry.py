"""Prompt registry for browsing and testing registered LLM prompts.

Scans the PROMPT_REGISTRY_DIR for `.prompt` files and provides lookup and
listing utilities used by the Prompt Catalog Streamlit page.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dagster_io.prompts import parse_prompt_file


@dataclass
class PromptEntry:
    """A prompt entry from the registry."""

    prompt_id: str
    model: str
    temperature: float
    max_tokens: int
    metadata: dict[str, Any]
    system_content: str
    domain: str = ""
    task: str = ""
    description: str = ""
    used_by: list[str] = field(default_factory=list)


def _get_registry_dir() -> Path | None:
    """Return the prompt registry directory, or None if not configured."""
    d = os.environ.get("PROMPT_REGISTRY_DIR")
    if d and Path(d).is_dir():
        return Path(d)
    return None


def list_prompts() -> list[PromptEntry]:
    """Scan PROMPT_REGISTRY_DIR recursively for `.prompt` files."""
    registry_dir = _get_registry_dir()
    if registry_dir is None:
        return []

    entries: list[PromptEntry] = []
    for prompt_path in sorted(registry_dir.rglob("*.prompt")):
        # Build prompt ID from relative path without extension
        rel = prompt_path.relative_to(registry_dir)
        prompt_id = str(rel.with_suffix(""))

        parsed = parse_prompt_file(prompt_path, prompt_id=prompt_id)
        meta = parsed.metadata

        entries.append(
            PromptEntry(
                prompt_id=parsed.prompt_id,
                model=parsed.model,
                temperature=parsed.temperature,
                max_tokens=parsed.max_tokens,
                metadata=meta,
                system_content=parsed.system_content,
                domain=meta.get("domain", ""),
                task=meta.get("task", ""),
                description=meta.get("description", ""),
                used_by=meta.get("used_by", []),
            )
        )

    return entries


def get_prompt(prompt_id: str) -> PromptEntry | None:
    """Look up a single prompt by ID (e.g. ``"rag/research-assistant"``)."""
    for entry in list_prompts():
        if entry.prompt_id == prompt_id:
            return entry
    return None
