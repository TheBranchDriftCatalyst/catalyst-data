"""Protocol definition for artifact repositories."""

from __future__ import annotations

from typing import Any, Protocol


class ArtifactRepository(Protocol):
    """Protocol for persisting extraction artifacts."""

    async def save_mentions(
        self, document_id: str, mentions: list[dict[str, Any]]
    ) -> None: ...

    async def save_propositions(
        self, document_id: str, propositions: list[dict[str, Any]]
    ) -> None: ...

    async def save_audit_trail(
        self, document_id: str, events: list[dict[str, Any]]
    ) -> None: ...
