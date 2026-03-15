"""Chunk-level incremental processing tracker.

Stores processed chunk content_hashes in S3 to enable skipping
unchanged chunks on re-runs. Used by LLM-intensive assets
(mentions, assertions) to avoid re-processing.
"""

from __future__ import annotations

import json

from dagster_io.logging import get_logger

logger = get_logger(__name__)


class ProcessingTracker:
    """Tracks which chunks have been processed via content hashes in S3.

    Usage:
        tracker = ProcessingTracker(s3_client, "gold/congress/mentions/_processed_hashes.json")
        unprocessed = tracker.filter_unprocessed(chunks)
        for chunk in unprocessed:
            # process chunk...
            tracker.mark_processed(chunk.content_hash)
        tracker.save()
    """

    def __init__(self, s3_client, tracker_key: str) -> None:
        self._s3 = s3_client
        self._key = tracker_key
        self._hashes: set[str] = set()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            data = self._s3.get_object(self._key)
            self._hashes = set(json.loads(data))
            logger.info("Loaded %d processed hashes from %s", len(self._hashes), self._key, extra={"tracker_key": self._key, "hash_count": len(self._hashes)})
        except Exception:
            self._hashes = set()
            logger.info("No existing tracker at %s — starting fresh", self._key, extra={"tracker_key": self._key})

    def filter_unprocessed(self, chunks: list) -> list:
        """Return only chunks whose content_hash is not yet processed."""
        unprocessed = [c for c in chunks if getattr(c, "content_hash", "") not in self._hashes]
        skipped = len(chunks) - len(unprocessed)
        logger.info("Tracker filter: %d total, %d unprocessed, %d skipped", len(chunks), len(unprocessed), skipped, extra={"total": len(chunks), "unprocessed": len(unprocessed), "skipped": skipped, "tracker_key": self._key})
        return unprocessed

    def mark_processed(self, content_hash: str) -> None:
        """Record a chunk as processed."""
        if content_hash and content_hash not in self._hashes:
            self._hashes.add(content_hash)
            self._dirty = True

    def save(self) -> None:
        """Persist the hash set back to S3."""
        if not self._dirty:
            logger.debug("Tracker not dirty, skipping save for %s", self._key)
            return
        payload = json.dumps(sorted(self._hashes), indent=0).encode("utf-8")
        self._s3.put_object(self._key, payload)
        self._dirty = False
        logger.info("Saved %d processed hashes to %s", len(self._hashes), self._key, extra={"tracker_key": self._key, "hash_count": len(self._hashes)})

    @property
    def processed_count(self) -> int:
        return len(self._hashes)
