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


def _repo_root() -> Path:
    """libs/dagster-io/src/dagster_io/prompts.py → catalyst-data."""
    return Path(__file__).resolve().parents[4]


def resolve_prompt_dir(
    *,
    domain: str | None = None,
    fallback: Path | None = None,
) -> str:
    """Return the prompt-registry directory for the active deployment.

    Resolution precedence:
      1. ``domain``-specific bundled prompts at
         ``k8s/base/<domain>/prompts`` — e.g. ``congress-data``,
         ``media-ingest``. When this exists, it wins: in dev the three
         code locations share one process so a single env var can't
         serve them, and in prod the containerised code locations don't
         carry the source tree at ``/app/prompts`` so this branch is a
         no-op and the env (step 2) wins instead.
      2. ``PROMPT_REGISTRY_DIR`` env var (set by per-domain k8s
         containers and Taskfile entries that need an explicit
         override).
      3. ``k8s/shared/prompts`` (the cross-domain registry) when no
         domain is specified.
      4. ``fallback`` when provided, else empty string.

    Returns:
        Absolute directory path as a string. Empty string when nothing
        resolves and no fallback is given — same contract the legacy
        ``os.environ.get(..., "")`` call sites already handle.
    """
    if domain:
        candidate = _repo_root() / "k8s" / "base" / domain / "prompts"
        if candidate.is_dir():
            return str(candidate)
    env_dir = os.environ.get("PROMPT_REGISTRY_DIR")
    if env_dir:
        return env_dir
    shared = _repo_root() / "k8s" / "shared" / "prompts"
    if shared.is_dir():
        return str(shared)
    if fallback is not None:
        return str(fallback)
    return ""


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
        max_tokens=frontmatter.get("max_tokens", 16384),
        metadata=frontmatter.get("metadata", {}),
    )


def load_prompt(
    prompt_id: str,
    fallback: str,
    *,
    registry_dir: str | None = None,
) -> str:
    """Load a prompt from the registry directory by ID.

    Parameters
    ----------
    prompt_id:
        Slash-separated identifier that maps to a file path under the
        registry directory.  For example, ``"ner/basic"`` resolves to
        ``<registry_dir>/ner/basic.prompt``.
    fallback:
        Returned immediately when no registry dir resolves or the file
        does not exist.  This keeps local development zero-cost.
    registry_dir:
        Explicit directory override. When passed, takes precedence over
        the ``PROMPT_REGISTRY_DIR`` env var. Use this from assets that
        already called ``resolve_prompt_dir(domain=...)`` — the domain-
        scoped path is more reliable than the global env var in the
        single-process multi-code-location dev rail.
    """
    registry_dir = registry_dir or os.environ.get("PROMPT_REGISTRY_DIR")
    if not registry_dir:
        logger.warning(
            "No prompt registry dir resolved (registry_dir + PROMPT_REGISTRY_DIR "
            "both empty), using fallback for prompt %r",
            prompt_id,
        )
        return fallback

    prompt_path = Path(registry_dir) / f"{prompt_id}.prompt"
    if not prompt_path.is_file():
        logger.warning(
            "Prompt file not found at %s, using fallback for prompt %r",
            prompt_path,
            prompt_id,
        )
        return fallback

    parsed = parse_prompt_file(prompt_path, prompt_id=prompt_id)
    return parsed.system_content
