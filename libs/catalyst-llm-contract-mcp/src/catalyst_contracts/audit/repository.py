from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditRepository:
    """Append-only JSONL audit log for contract validation results."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = Path.home() / ".catalyst" / "contract-audit.jsonl"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        tool_name: str,
        verdict: str,
        payload: dict[str, Any],
        error_count: int,
        accepted: bool,
    ) -> dict[str, Any]:
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": tool_name,
            "verdict": verdict,
            "payload_hash": payload_hash,
            "error_count": error_count,
            "accepted": accepted,
        }

        with self._path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

        return entry

    def read_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        entries = []
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
