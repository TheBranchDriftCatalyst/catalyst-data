"""Append-only JSONL repository for extraction artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import aiofiles

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")


class JsonlRepository:
    """Append-only JSONL file per document_id and artifact type."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    @staticmethod
    def _validate_document_id(document_id: str) -> None:
        """Reject document_ids that could cause path traversal."""
        if not document_id or not _SAFE_ID_RE.match(document_id):
            raise ValueError(
                f"Invalid document_id: {document_id!r}. "
                "Must be non-empty and contain only alphanumeric characters, "
                "hyphens, underscores, or dots (no path separators or '..')."
            )

    async def _append(
        self, document_id: str, artifact_type: str, records: list[dict[str, Any]]
    ) -> None:
        self._validate_document_id(document_id)
        dir_path = self._base_dir / document_id
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{artifact_type}.jsonl"

        async with aiofiles.open(file_path, mode="a", encoding="utf-8") as f:
            for record in records:
                await f.write(json.dumps(record, default=str) + "\n")

    async def save_mentions(
        self, document_id: str, mentions: list[dict[str, Any]]
    ) -> None:
        await self._append(document_id, "mentions", mentions)

    async def save_propositions(
        self, document_id: str, propositions: list[dict[str, Any]]
    ) -> None:
        await self._append(document_id, "propositions", propositions)

    async def save_audit_trail(
        self, document_id: str, events: list[dict[str, Any]]
    ) -> None:
        await self._append(document_id, "audit_trail", events)
