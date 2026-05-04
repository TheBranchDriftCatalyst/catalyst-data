"""Shared audit-event helper for exgraph nodes.

Dual-writes: the returned dict goes into ``state["audit_events"]``
(post-hoc trail) and the same fields are emitted to the DuckDB-backed
bench audit log with a per-node state summary the StateInspector
consumes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dagster_io import event_store


def _compact(item: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage == "ner":
        return {
            "text": item.get("text"),
            "type": item.get("mention_type") or item.get("entity_type"),
            "span": [item.get("span_start"), item.get("span_end")],
            "conf": item.get("confidence"),
        }
    return {
        "subject": item.get("subject"),
        "predicate": item.get("predicate"),
        "object": item.get("object"),
        "conf": item.get("confidence"),
    }


def _provenance_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    fields = ("document_id", "chunk_id", "extraction_model", "speaker_label", "temporal_start_ms")
    counts = dict.fromkeys(fields, 0)
    span_count = 0
    for it in items:
        prov = it.get("provenance") or {}
        for f in fields:
            if prov.get(f) or it.get(f):
                counts[f] += 1
        if (it.get("span_start") is not None and it.get("span_end") is not None) or (
            prov.get("span_start") is not None
        ):
            span_count += 1
    counts["has_span"] = span_count
    counts["total"] = len(items)
    return counts


def _state_summary(node_name: str, stage_state: dict[str, Any], stage: str) -> dict[str, Any]:
    s: dict[str, Any] = {}
    if node_name.startswith("extract_"):
        cands = stage_state.get("candidates") or []
        s["candidate_count"] = len(cands)
        s["candidate_sample"] = [_compact(c, stage) for c in cands[:3]]
    elif node_name.startswith("validate_"):
        v = stage_state.get("validation") or {}
        s["verdict"] = v.get("verdict")
        s["valid_count"] = v.get("valid_count")
        s["invalid_count"] = v.get("invalid_count")
        s["errors"] = v.get("errors", [])[:20]
    elif node_name.startswith("repair_"):
        s["retry_count"] = stage_state.get("retry_count")
        s["repaired_count"] = len(stage_state.get("candidates") or [])
    return s


def make_audit_event(
    node_name: str,
    status: str,
    *,
    state: dict[str, Any] | None = None,
    duration_s: float | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Create an audit event for an exgraph node and emit it to the tail.

    Phase 2 (CD-j6d3): reads ``state["evidence_window_id"]`` and propagates
    it through the emitted event.  NER-scoped events leave it ``None``; SPO
    events (running inside an evidence window) carry the window id so the
    StateInspector can group events by ``(model, doc_id, evidence_window_id)``.
    """
    s = state or {}
    model = s.get("model")
    src_meta = s.get("source_metadata") or {}
    doc_id = s.get("doc_id") or src_meta.get("document_id")
    chunk_idx = s.get("chunk_idx")
    chunk_id = s.get("chunk_id") or src_meta.get("chunk_id")
    # LangGraph's state propagation drops top-level TypedDict fields that
    # weren't part of the original input dict in some configs (1.1.x). Read
    # from source_metadata as a fallback — that's a nested dict, so it
    # survives whatever filtering LangGraph applies. Phase 2 originally
    # wrote evidence_window_id only at the top level, which silently lost it.
    evidence_window_id: str | None = s.get("evidence_window_id") or src_meta.get("evidence_window_id")

    stage_name = ""
    for prefix in ("extract_", "validate_", "repair_"):
        if node_name.startswith(prefix):
            stage_name = node_name[len(prefix) :]
            break
    stage_state = s.get("stages", {}).get(stage_name, {}) if stage_name else {}
    retry_count = stage_state.get("retry_count")

    state_summary = _state_summary(node_name, stage_state, stage_name)

    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "node_name": node_name,
        "status": status,
        "duration_s": duration_s,
        "model": model,
        "doc_id": doc_id,
        "chunk_idx": chunk_idx,
        "chunk_id": chunk_id,
        "retry_count": retry_count,
        "evidence_window_id": evidence_window_id,
        "details": details,
    }

    event_store.append(
        source="exgraph",
        node_name=node_name,
        status=status,
        model=model,
        doc_id=doc_id,
        chunk_idx=chunk_idx,
        chunk_id=chunk_id,
        retry_count=retry_count,
        evidence_window_id=evidence_window_id,
        state=state_summary,
        details={**details, "duration_s": duration_s} if duration_s is not None else details,
    )

    return event
