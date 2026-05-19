"""Thread-local capture buffer for SPO LLM calls (Gap #5).

The SPO ExtractNode wants prompt-hash, raw-response, token usage, and
cost on every LLM call so the State Inspector can render a "show full
prompt" pane. The cleanest seam is the LLM client's ``structured_output``
because that's the only place with access to BOTH the rendered prompt
*and* the raw model response. But ``structured_output`` is shared with
non-SPO call sites (NER repair, custom flows), so we don't want to make
audit-emit unconditional there.

Solution: a thread-local buffer that the SPO ExtractNode opens before
calling the client and reads back after. The client checks the buffer
and writes the raw text + usage_metadata into it whenever it is open.
Other call sites leave the buffer ``None`` and the client skips the
capture entirely — zero overhead off the SPO path.

Usage:

    with open_capture() as cap:
        result = await client.structured_output(schema, messages)
    raw_text = cap.raw_text
    usage = cap.usage           # {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
    error = cap.parsing_error   # raw exception from the recovery path, if any

The capture is thread-local so concurrent SPO doc-tasks (when the legacy
SPO LLM path was active, fanned out via ``ThreadPoolExecutor``) don't
cross-pollinate. Wave 1 Step 4 (bead llm-g0b) retired that path —
the AMR-as-spine pipeline uses ``ExtractionResource.extract_assertions``
which doesn't open this capture. The module is kept for any
non-extraction caller still using ``open_capture()`` directly; it is
otherwise dormant on the new AMR path.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _CaptureSlot:
    """Per-call capture buffer. Reset on each ``open_capture`` entry."""

    raw_text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    parsing_error: Any = None


_local = threading.local()


def _slot() -> _CaptureSlot | None:
    return getattr(_local, "slot", None)


def is_capturing() -> bool:
    """True when the current thread has an open capture slot.

    LLMClient.structured_output should branch on this; when False the
    client must not pay any capture overhead.
    """
    return _slot() is not None


def write(raw_text: str, usage: dict[str, int] | None = None, parsing_error: Any = None) -> None:
    """Called by the LLM client after a structured_output call.

    No-op when there's no open capture (non-SPO call sites).
    """
    slot = _slot()
    if slot is None:
        return
    slot.raw_text = raw_text or ""
    slot.usage = dict(usage or {})
    slot.parsing_error = parsing_error


@contextlib.contextmanager
def open_capture() -> Iterator[_CaptureSlot]:
    """Open a capture slot for the duration of one LLM call.

    Re-entrancy: nested ``open_capture`` calls in the same thread shadow
    the outer slot; the inner exit restores it. We don't expect this in
    practice (one LLM call per ExtractNode invocation) but it keeps the
    API forgiving for tests that wrap the helper.
    """
    prev = _slot()
    slot = _CaptureSlot()
    _local.slot = slot
    try:
        yield slot
    finally:
        if prev is None:
            with contextlib.suppress(AttributeError):
                del _local.slot
        else:
            _local.slot = prev


__all__ = ["is_capturing", "open_capture", "write"]
