"""Prompt loading utilities for the prompt registry.

Loads `.prompt` files with YAML frontmatter for metadata followed by the
prompt body. Standalone version (no dagster dependency).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ParsedPrompt:
    """A parsed .prompt file with metadata and content."""

    prompt_id: str
    system_content: str
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: int = 16384
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_prompt_file(path: Path, prompt_id: str | None = None) -> ParsedPrompt:
    """Parse a `.prompt` file into metadata and system content."""
    raw = path.read_text(encoding="utf-8")

    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
        else:
            frontmatter = {}
            body = raw.strip()
    else:
        frontmatter = {}
        body = raw.strip()

    pid = prompt_id or path.stem

    return ParsedPrompt(
        prompt_id=pid,
        system_content=body,
        model=frontmatter.get("model", "gpt-4o-mini"),
        temperature=frontmatter.get("temperature", 0.0),
        max_tokens=frontmatter.get("max_tokens", 16384),
        metadata=frontmatter.get("metadata", {}),
    )


def strip_think_blocks(text: str) -> str:
    """Strip ``<think>...</think>`` blocks from reasoning-model output.

    Models like DeepSeek-R1 and Qwen3 (thinking mode) emit chain-of-thought
    wrapped in ``<think>`` tags before the actual response::

        <think>
        Let me analyze the text...
        </think>
        {"mentions": [...]}

    This strips all such blocks (including nested/multiple) and returns only
    the content outside the tags.  Safe to call on output that has no think
    tags — it returns the input unchanged.
    """
    import re

    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def strip_code_fences(text: str) -> str:
    """Strip markdown code fences from LLM output.

    LLMs often wrap JSON in ```json ... ``` blocks.
    This extracts the content inside the fences.
    """
    import re

    text = text.strip()
    m = re.match(r"^```(?:json|JSON)?\s*\n(.*?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def load_prompt(prompt_id: str, fallback: str) -> str:
    """Load a prompt from the registry directory by ID.

    Parameters
    ----------
    prompt_id:
        Slash-separated identifier that maps to a file path under the
        registry directory.  For example, ``"mention_extraction"`` resolves
        to ``<PROMPT_REGISTRY_DIR>/mention_extraction.prompt``.
    fallback:
        Returned when PROMPT_REGISTRY_DIR is not set or the file is missing.
    """
    registry_dir = os.environ.get("PROMPT_REGISTRY_DIR")
    if not registry_dir:
        return fallback

    prompt_path = Path(registry_dir) / f"{prompt_id}.prompt"
    if not prompt_path.is_file():
        logger.warning("Prompt file not found at %s, using fallback for %r", prompt_path, prompt_id)
        return fallback

    parsed = parse_prompt_file(prompt_path, prompt_id=prompt_id)
    return parsed.system_content
