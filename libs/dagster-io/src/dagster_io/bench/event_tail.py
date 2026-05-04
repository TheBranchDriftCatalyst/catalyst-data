"""Unified event-stream writer.

One JSONL file per run captures every observable event from harness,
exgraph, langgraph, and dagster. Configured once per process at startup
via ``configure(path, run_id=...)``; all subsequent ``append(...)``
calls write to that file.

Live consumers (the run-bus WS server) tail the JSONL; post-hoc
consumers (the viewer) replay the same file. Same shape, same source
of truth — no per-source flush paths, no env-var gating.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

_path: Path | None = None
_run_id: str | None = None
_lock = Lock()
_seen_chunks: set[str] = set()

# Background uploader state — populated by ``configure_periodic_upload``.
# Daemon thread is auto-killed on process exit; we still expose
# ``stop_periodic_upload`` for clean shutdown before final archive.
_uploader_thread: threading.Thread | None = None
_uploader_stop = threading.Event()
_uploader_fn: Callable[[Path], None] | None = None
_last_uploaded_size: int = 0


def configure(path: str | Path, *, run_id: str) -> None:
    """Bind the writer to a run.

    Must be called once per process before any ``append(...)``. Idempotent
    when called with the same path+run_id; raises if reconfigured to a
    different target mid-run (signals a wiring bug, not a runtime case).
    """
    global _path, _run_id
    new_path = Path(path)
    if _path is not None and (_path != new_path or _run_id != run_id):
        raise RuntimeError(
            f"event_tail already configured for run_id={_run_id!r} at {_path}; "
            f"refusing to retarget to run_id={run_id!r} at {new_path}"
        )
    new_path.parent.mkdir(parents=True, exist_ok=True)
    _path = new_path
    _run_id = run_id


def is_configured() -> bool:
    return _path is not None


def current_run_id() -> str | None:
    return _run_id


def append(
    *,
    source: str,
    node_name: str,
    status: str,
    model: str | None = None,
    doc_id: str | None = None,
    chunk_idx: int | None = None,
    chunk_id: str | None = None,
    retry_count: int | None = None,
    code_location: str | None = None,
    evidence_window_id: str | None = None,
    state: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Append a single event to the run's JSONL.

    Required fields (``source``, ``node_name``, ``status``) plus the
    optional context fields define the unified event shape that the
    viewer's LiveGantt, AuditViewer, and StateInspector all consume.

    The ``state`` field carries a small per-node summary (verdict,
    candidate_count, errors, retry delta, provenance completeness) —
    bounded so the JSONL stays scannable. Full chunk text is emitted
    once per chunk via ``emit_chunk_text`` and joined by ``chunk_id``.

    Phase 2 (CD-j6d3): ``evidence_window_id`` is set for SPO-scoped events
    (running inside an evidence window) and ``None`` for NER-scoped events.
    The StateInspector will use this to group events by
    ``(model, doc_id, evidence_window_id)`` in a follow-up issue.
    """
    if _path is None:
        raise RuntimeError("event_tail.configure() must be called before append()")

    record = {
        "ts": datetime.now(UTC).isoformat(),
        "run_id": _run_id,
        "source": source,
        "node_name": node_name,
        "status": status,
        "model": model,
        "doc_id": doc_id,
        "chunk_idx": chunk_idx,
        "chunk_id": chunk_id,
        "retry_count": retry_count,
        "code_location": code_location,
        "evidence_window_id": evidence_window_id,
        "state": state or {},
        "details": details or {},
    }
    line = json.dumps(record, default=str)

    with _lock, _path.open("a") as f:
        f.write(line + "\n")


def emit_chunk_text(
    chunk_id: str,
    text: str,
    *,
    doc_id: str | None = None,
    model: str | None = None,
    domain: str | None = None,
    speaker_label: str | None = None,
    temporal_start_ms: float | None = None,
    temporal_end_ms: float | None = None,
    chunk_index: int | None = None,
    total_chunks: int | None = None,
    chunk_metadata: dict[str, Any] | None = None,
    max_chars: int = 4096,
) -> None:
    """Emit a one-shot ``chunk_loaded`` event the first time a chunk_id
    is seen in this process. Idempotent — subsequent calls for the same
    chunk_id are no-ops, so repair retries don't re-emit. The text is
    capped at ``max_chars`` (default 4 KiB) and a ``truncated`` flag is
    set when the source was longer; the StateInspector uses the inline
    text directly without a side fetch.

    ``chunk_metadata`` carries the chunker's strategy + size/overlap /
    char-offset / content-hash so the StateInspector right-pane can
    surface "why is this chunk shaped this way" without re-reading the
    silver layer. Index + total flow through separately because they're
    promoted onto the TextChunk model itself, not the metadata bag.
    """
    if not chunk_id or chunk_id in _seen_chunks:
        return
    _seen_chunks.add(chunk_id)
    truncated = len(text) > max_chars
    append(
        source="harness",
        node_name="chunk_loaded",
        status="info",
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        details={
            "text": text[:max_chars],
            "char_count": len(text),
            "truncated": truncated,
            "domain": domain,
            "speaker_label": speaker_label,
            "temporal_start_ms": temporal_start_ms,
            "temporal_end_ms": temporal_end_ms,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "chunk_metadata": chunk_metadata or {},
        },
    )


def emit_chunk_extracted(
    chunk_id: str,
    *,
    model: str | None = None,
    doc_id: str | None = None,
    mentions: list[dict[str, Any]] | None = None,
    propositions: list[dict[str, Any]] | None = None,
) -> None:
    """Emit a terminal ``chunk_extracted`` event with the final NER +
    SPO output for a (model, chunk) pair. Lets the StateInspector tie
    the chunk text directly to what each model produced without
    reconstructing it from intermediate validate/repair events.
    """
    append(
        source="harness",
        node_name="chunk_extracted",
        status="completed",
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        details={
            "mentions": mentions or [],
            "propositions": propositions or [],
            "mention_count": len(mentions) if mentions else 0,
            "proposition_count": len(propositions) if propositions else 0,
        },
    )


def configure_periodic_upload(
    uploader: Callable[[Path], None],
    *,
    interval_s: float = 5.0,
) -> None:
    """Start a background thread that uploads the JSONL every ``interval_s``.

    The uploader callable receives the local Path and should put it to
    whatever backing store it likes (typically S3). Only fires when the
    file's size has grown since the last upload — avoids redundant PUTs
    during idle stretches.

    No-op if already running, if event_tail isn't configured, or if the
    process is in a state that can't spawn threads. Uploader exceptions
    are logged to stderr but don't bring the run down — a transient S3
    hiccup shouldn't fail a 30-min benchmark.
    """
    global _uploader_thread, _uploader_fn
    if _uploader_thread is not None or _path is None:
        return
    _uploader_fn = uploader
    _uploader_stop.clear()

    def _loop() -> None:
        global _last_uploaded_size
        while not _uploader_stop.wait(interval_s):
            try:
                if _path is None or not _path.exists():
                    continue
                size = _path.stat().st_size
                if size <= _last_uploaded_size or size == 0:
                    continue
                if _uploader_fn is not None:
                    _uploader_fn(_path)
                    _last_uploaded_size = size
            except Exception as e:  # noqa: BLE001
                print(f"event_tail uploader: {e}", file=sys.stderr)

    t = threading.Thread(target=_loop, daemon=True, name="event-tail-uploader")
    t.start()
    _uploader_thread = t


def stop_periodic_upload(*, final_flush: bool = True) -> None:
    """Stop the background uploader. Optionally do one final upload first
    so the post-run archive doesn't lag the last few events.

    Safe to call when no uploader was ever started — no-op in that case.
    """
    global _uploader_thread, _uploader_fn, _last_uploaded_size
    if _uploader_thread is None:
        return
    _uploader_stop.set()
    _uploader_thread.join(timeout=5.0)
    _uploader_thread = None
    if final_flush and _path is not None and _path.exists() and _uploader_fn is not None:
        try:
            if _path.stat().st_size > _last_uploaded_size:
                _uploader_fn(_path)
                _last_uploaded_size = _path.stat().st_size
        except Exception as e:  # noqa: BLE001
            print(f"event_tail final flush: {e}", file=sys.stderr)
    _uploader_fn = None


def configure_from_env() -> None:
    """Convenience for subprocess entry points (pytest conftest, scripts).

    Reads ``CATALYST_RUN_DIR`` and ``CATALYST_RUN_ID`` from the environment.
    No-op if the env vars are unset, so direct ``python`` invocations of
    library code outside a configured run don't bind the writer. Subprocess
    child processes whose parent set both vars get auto-configured.
    """
    run_dir = os.environ.get("CATALYST_RUN_DIR")
    run_id = os.environ.get("CATALYST_RUN_ID")
    if run_dir and run_id and not is_configured():
        configure(Path(run_dir) / "events.jsonl", run_id=run_id)
