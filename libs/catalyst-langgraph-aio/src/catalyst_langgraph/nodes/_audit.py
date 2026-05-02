"""Audit-event helper: dual-writes to in-state ``audit_events`` and the
unified event-tail JSONL.

Every langgraph node calls this once per outcome (completed / error /
verdict). The returned dict is appended to ``state["audit_events"]`` so
post-hoc inspection still sees the full trail; the same event is also
emitted to the run's events.jsonl via ``dagster_io.event_tail``, with a
small per-node ``state`` summary so the StateInspector page can show
provenance + verdict + retry deltas without a side fetch.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from catalyst_langgraph.state import AuditEvent
from dagster_io import event_tail


def _compact_mention(m: dict[str, Any]) -> dict[str, Any]:
    """Tiny mention summary for `state.candidate_sample` — span + type
    + text + confidence is enough to spot-check extraction quality
    without dumping the full record."""
    return {
        "text": m.get("text"),
        "type": m.get("mention_type") or m.get("entity_type"),
        "span": [m.get("span_start"), m.get("span_end")],
        "conf": m.get("confidence"),
    }


def _compact_proposition(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject": p.get("subject"),
        "predicate": p.get("predicate"),
        "object": p.get("object"),
        "conf": p.get("confidence"),
    }


def _provenance_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    """Count how many items have each provenance field populated. Used
    by the StateInspector's provenance heatmap to flag gaps before the
    record reaches the knowledge graph."""
    fields = (
        "document_id",
        "chunk_id",
        "extraction_model",
        "speaker_label",
        "temporal_start_ms",
    )
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


def _state_summary(node_name: str, full: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    """Build the small per-node state slice. Bounded so the JSONL line
    stays scannable: candidate samples are capped at 3, error lists at
    20, no full payloads."""
    summary: dict[str, Any] = {}
    if node_name.startswith("extract_"):
        cands = (
            full.get("current_mention_candidates")
            if "mention" in node_name
            else full.get("current_proposition_candidates")
        ) or []
        compactor = _compact_mention if "mention" in node_name else _compact_proposition
        summary["candidate_count"] = len(cands)
        summary["candidate_sample"] = [compactor(c) for c in cands[:3]]
    elif node_name.startswith("validate_"):
        v = (
            full.get("latest_mention_validation")
            if "mention" in node_name
            else full.get("latest_proposition_validation")
        ) or {}
        summary["verdict"] = v.get("verdict")
        summary["valid_count"] = v.get("valid_count")
        summary["invalid_count"] = v.get("invalid_count")
        summary["errors"] = v.get("errors", [])[:20]
    elif node_name.startswith("repair_"):
        summary["retry_count"] = (
            full.get("mention_retry_count") if "mention" in node_name else full.get("proposition_retry_count")
        )
        if "delta" in details:
            summary["delta"] = details["delta"]
        summary["repaired_count"] = details.get("repaired_count")
    elif node_name == "persist_artifacts":
        accepted_m = full.get("accepted_mentions") or []
        accepted_p = full.get("accepted_propositions") or []
        summary["mentions_saved"] = len(accepted_m)
        summary["propositions_saved"] = len(accepted_p)
        summary["mention_provenance"] = _provenance_summary(accepted_m)
        summary["proposition_provenance"] = _provenance_summary(accepted_p)
    return summary


def make_audit_event(
    node_name: str,
    status: str,
    *,
    state: dict[str, Any] | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Create an audit event and emit it to the run's event tail.

    ``state`` is the current ``ExtractionState`` so we can pull
    ``model``, ``doc_id``, ``chunk_idx``, ``mention_retry_count`` /
    ``proposition_retry_count``, and synthesize the per-node state
    summary in one place.
    """
    s = state or {}
    model = s.get("model")
    src_meta = s.get("source_metadata") or {}
    doc_id = s.get("doc_id") or src_meta.get("document_id")
    chunk_idx = s.get("chunk_idx")
    chunk_id = s.get("chunk_id") or src_meta.get("chunk_id")
    retry_count = (
        s.get("mention_retry_count")
        if "mention" in node_name or "ner" in node_name
        else s.get("proposition_retry_count")
        if "proposition" in node_name or "spo" in node_name
        else None
    )

    state_summary = _state_summary(node_name, s, details)

    event = AuditEvent(
        node_name=node_name,
        status=status,
        model=model,
        doc_id=doc_id,
        chunk_idx=chunk_idx,
        retry_count=retry_count,
        details=details,
    )

    event_tail.append(
        source="langgraph",
        node_name=node_name,
        status=status,
        model=model,
        doc_id=doc_id,
        chunk_idx=chunk_idx,
        chunk_id=chunk_id,
        retry_count=retry_count,
        state=state_summary,
        details=details,
    )

    return asdict(event)
