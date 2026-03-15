"""Prompt loading utilities for the prompt registry.

Loads `.prompt` files from a directory specified by the PROMPT_REGISTRY_DIR
environment variable.  Each file uses YAML frontmatter for metadata followed
by the prompt body.

In local development, if the env var is unset or the file is missing, the
provided fallback string is returned immediately — zero-cost default.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import yaml


@dataclass
class ParsedPrompt:
    """A parsed .prompt file with metadata and content."""

    prompt_id: str
    system_content: str
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: int = 4096
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_prompt_file(path: Path, prompt_id: str | None = None) -> ParsedPrompt:
    """Parse a `.prompt` file into metadata and system content.

    The file format is YAML frontmatter (delimited by ``---``) followed by the
    prompt body.
    """
    raw = path.read_text(encoding="utf-8")

    # Split on frontmatter delimiters
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
        max_tokens=frontmatter.get("max_tokens", 4096),
        metadata=frontmatter.get("metadata", {}),
    )


def load_prompt(prompt_id: str, fallback: str) -> str:
    """Load a prompt from the registry directory by ID.

    Parameters
    ----------
    prompt_id:
        Slash-separated identifier that maps to a file path under the
        registry directory.  For example, ``"ner/basic"`` resolves to
        ``<PROMPT_REGISTRY_DIR>/ner/basic.prompt``.
    fallback:
        Returned immediately when ``PROMPT_REGISTRY_DIR`` is not set or the
        file does not exist.  This keeps local development zero-cost.

    Returns
    -------
    str
        The system prompt body (everything after YAML frontmatter).
    """
    registry_dir = os.environ.get("PROMPT_REGISTRY_DIR")
    if not registry_dir:
        logger.warning("PROMPT_REGISTRY_DIR not set, using fallback for prompt %r", prompt_id)
        return fallback

    prompt_path = Path(registry_dir) / f"{prompt_id}.prompt"
    if not prompt_path.is_file():
        logger.warning("Prompt file not found at %s, using fallback for prompt %r", prompt_path, prompt_id)
        return fallback

    parsed = parse_prompt_file(prompt_path, prompt_id=prompt_id)
    return parsed.system_content
