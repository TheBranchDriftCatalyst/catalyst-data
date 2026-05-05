"""Generic extraction node — parameterized by StageConfig.

Replaces both ExtractMentions and ExtractPropositions with a single
configurable node that works for any extraction type.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.nodes._audit import make_audit_event
from catalyst_exgraph.nodes.spans import correct_candidate_spans
from catalyst_exgraph.protocol import ExtractionClient
from catalyst_exgraph.state import ExGraphState, ExGraphStatus
from dagster_io import event_store

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# SPO prompt/response capture (Gap #5)
#
# Background ThreadPoolExecutor for S3 puts so a slow MinIO doesn't gate
# the SPO loop. Audit-log emit (event_store.append) STAYS synchronous —
# it's the bench's source of truth and must land on disk before we move
# on. The S3-archive write is best-effort and tolerates being delayed.
#
# Sized small (4) so a flaky MinIO can't bloat memory with queued futures.
# Runtime futures are not awaited explicitly; they drain on process exit.
# A future ``flush_pending_uploads()`` helper could join them at run-end
# if archive durability becomes an issue — for now, the bench harness
# closes by re-fetching from the audit log, not the archive.
# ─────────────────────────────────────────────────────────────────────────

_SPO_S3_EXECUTOR: ThreadPoolExecutor | None = None
_SPO_PROMPT_PREVIEW_CHARS = 2048
_SPO_RESPONSE_PREVIEW_HEAD = 1024
_SPO_RESPONSE_PREVIEW_TAIL = 512
_SPO_RESPONSE_INLINE_THRESHOLD = 1536  # ~1.5 KB; above this we elide.

# Per-chunk SPO capture buffer. The ExtractNode populates this when it
# runs the SPO LLM call; the doc-level SPO orchestrator
# (``_process_doc_spo_only`` in dagster_io.extraction) drains the entry
# right before emitting the chunk_extracted event. Kept module-global
# because the LangGraph state-merge path strips unknown keys, and we
# want the change to be additive (no new public state schema field).
#
# Thread-safety: the SPO loop is per-doc-task, parallelised at the doc
# level; within a task, ExtractNode invocations are sequential. So
# concurrent writes to the same chunk_id never happen. Writes from
# different chunk_ids are append-only on a builtin dict — Python's GIL
# guarantees atomicity for ``dict[k] = v``.
_SPO_CAPTURE_BUFFER: dict[str, dict[str, Any]] = {}


def consume_spo_capture(chunk_id: str) -> dict[str, Any] | None:
    """Pop and return the SPO capture details for ``chunk_id``, if any.

    Called by the doc-level SPO orchestrator right before
    ``event_store.emit_chunk_extracted`` so the prompt-hash / preview /
    usage / cost / parse_errors fields land in the event's ``details``
    blob. Returns ``None`` for non-SPO chunks or when capture is
    disabled (no env, no LLM client, etc.).
    """
    return _SPO_CAPTURE_BUFFER.pop(chunk_id, None)


def _get_spo_s3_executor() -> ThreadPoolExecutor:
    global _SPO_S3_EXECUTOR
    if _SPO_S3_EXECUTOR is None:
        _SPO_S3_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="spo-s3-archive")
    return _SPO_S3_EXECUTOR


def _archive_prompt_and_response(
    prompt_hash: str,
    rendered_prompt: str,
    run_id: str,
    chunk_id: str,
    raw_response: str,
) -> None:
    """Write the full prompt + raw response to S3 (best-effort).

    Runs on the background executor so MinIO latency doesn't gate the
    SPO loop. Any S3 error is logged and swallowed — the inline
    preview in the audit log is the user-visible fallback.
    """
    try:
        from dagster_io.bench.prompt_store import put_prompt, put_response  # noqa: PLC0415
        from dagster_io.bench.store import S3BenchmarkStore  # noqa: PLC0415

        store = S3BenchmarkStore()
        put_prompt(store, prompt_hash, rendered_prompt)
        put_response(store, run_id, chunk_id, raw_response)
    except Exception as e:  # noqa: BLE001 — best-effort archive
        logger.warning("spo-archive write failed for chunk_id=%s: %s", chunk_id, e)


def _hash_prompt(system: str, user: str) -> str:
    """sha256(system + "\n\n" + user) truncated to 16 hex chars."""
    h = hashlib.sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\n\n")
    h.update(user.encode("utf-8"))
    return h.hexdigest()[:16]


def _truncate_response_preview(raw: str) -> str:
    """Inline preview = first 1024 + last 512 with elision when long."""
    if len(raw) <= _SPO_RESPONSE_INLINE_THRESHOLD:
        return raw
    head = raw[:_SPO_RESPONSE_PREVIEW_HEAD]
    tail = raw[-_SPO_RESPONSE_PREVIEW_TAIL:]
    elided = len(raw) - len(head) - len(tail)
    return f"{head}…[{elided} chars elided]…{tail}"


def _classify_parse_errors(parsing_error: Any, candidates: list[dict], raw_text: str) -> list[dict]:
    """Build the ``parse_errors`` taxonomy for one SPO call.

    Stages:
      ``json``   — JSON.parse failed (raw text was not parseable JSON).
      ``schema`` — JSON parsed but didn't validate against the schema.
      ``empty``  — parsed cleanly but produced zero candidates while the
                   prompt expected output.

    Multiple stages can fire on the same call (e.g. raw was unparseable
    *and* the recovery path landed on an empty list).
    """
    errors: list[dict] = []
    if parsing_error is not None:
        msg = str(parsing_error)
        # LangChain wraps both JSON and pydantic errors; cheap heuristic
        # on the message keeps us from importing langchain types here.
        if "JSON" in msg or "Expecting" in msg or "json" in msg.lower():
            errors.append({"stage": "json", "message": msg[:500]})
        else:
            errors.append({"stage": "schema", "message": msg[:500]})
    if not candidates and raw_text:
        # Distinguish "valid empty list returned" from "model said
        # nothing": only flag empty when the model produced output.
        errors.append({"stage": "empty", "message": "0 candidates from non-empty raw response"})
    return errors


def _format_entity_provenance(mentions: list[dict]) -> str:
    """Format a list of mention dicts into a human-readable entity block.

    When mentions carry consensus metadata (``vote_count`` + ``n_encoders``
    fields), the richer provenance format is used:

        - Reagan           [PERSON,      5/5 votes, mean_conf 0.94]
        - Crimea           [LOCATION,    3/5 votes, mean_conf 0.62]

    Legacy mentions (bare ``{text, mention_type}`` shape) fall back to:

        - Reagan           [PERSON]

    Both shapes are tolerated in the same list so mixed-pipeline paths don't
    crash.  Empty or missing ``text`` entries are skipped silently.
    """
    if not mentions:
        return "  (none)"

    lines: list[str] = []
    for m in mentions:
        text = m.get("text", "")
        if not text:
            continue

        # Detect ConsensusMention shape
        if "vote_count" in m and "n_encoders" in m:
            entity_type = m.get("canonical_type") or m.get("mention_type") or "ENTITY"
            vote_count = m.get("vote_count", 0)
            n_encoders = m.get("n_encoders", 1)
            mean_conf = m.get("mean_confidence", 0.0)
            lines.append(f"  - {text:<30s} [{entity_type}, {vote_count}/{n_encoders} votes, mean_conf {mean_conf:.2f}]")
        else:
            entity_type = m.get("mention_type") or m.get("canonical_type") or "ENTITY"
            lines.append(f"  - {text:<30s} [{entity_type}]")

    return "\n".join(lines) if lines else "  (none)"


def _load_prompt(config: StageConfig) -> str:
    """Load a prompt from config.prompt_dir or fall back to PROMPT_REGISTRY_DIR env var."""
    from pathlib import Path

    # Try config.prompt_dir first (explicit > env var)
    if config.prompt_dir:
        prompt_path = Path(config.prompt_dir) / f"{config.prompt_id}.prompt"
        if prompt_path.is_file():
            from catalyst_langgraph.prompts import parse_prompt_file

            return parse_prompt_file(prompt_path, config.prompt_id).system_content

    # Fall back to env var
    from catalyst_langgraph.prompts import load_prompt

    return load_prompt(config.prompt_id, config.fallback_prompt)


class ExtractNode:
    """Generic extraction node.

    Calls client.structured_output() with the schema and prompt from StageConfig.
    Populates state["stages"][config.stage_name] with candidates.

    For SPO stages, reads accepted items from upstream stages via
    state["upstream_context"].
    """

    def __init__(self, config: StageConfig, client: ExtractionClient) -> None:
        self.config = config
        self.client = client

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        raw_text = state.get("raw_text", "")
        stage_name = self.config.stage_name
        node_name = f"extract_{stage_name}"

        src = state.get("source_metadata") or {}
        chunk_id = state.get("chunk_id") or src.get("chunk_id")
        if chunk_id:
            event_store.emit_chunk_text(
                chunk_id,
                raw_text,
                doc_id=state.get("doc_id") or src.get("document_id"),
                model=state.get("model"),
                domain=src.get("domain"),
                speaker_label=src.get("speaker_label"),
                temporal_start_ms=src.get("temporal_start_ms"),
                temporal_end_ms=src.get("temporal_end_ms"),
                chunk_index=src.get("chunk_index"),
                total_chunks=src.get("total_chunks"),
                chunk_metadata=src.get("chunk_metadata") or {},
            )

        logger.info("%s: start, input_len=%d", node_name, len(raw_text))
        t0 = time.perf_counter()

        try:
            # Load prompt from config.prompt_dir or PROMPT_REGISTRY_DIR env var
            system = _load_prompt(self.config)

            # Build human message — for SPO stages, include upstream NER as constraints
            if self.config.stage_name == "spo":
                upstream = state.get("upstream_context", {})
                accepted_mentions = upstream.get("accepted_mentions", [])
                # Format entity provenance block — includes vote_count / mean_confidence
                # when consensus metadata is present; falls back to bare "text [type]"
                # format for legacy single-NER pipelines.
                entity_block = _format_entity_provenance(accepted_mentions)
                prompt = f"Entities (with NER agreement):\n{entity_block}\n\nInput text: {raw_text}"
            else:
                prompt = raw_text

            # SPO bench capture (Gap #5): open a thread-local capture slot
            # so LLMClient.structured_output writes the raw response +
            # usage metadata back to us. Non-SPO stages and non-LLM clients
            # leave this dormant and the client skips the capture entirely.
            #
            # Imported lazily so this module stays usable in environments
            # that haven't installed dagster_io (rare, but the existing
            # pattern in this file already lazy-imports event_store
            # equivalents for the same reason).
            _capture_spo = self.config.stage_name == "spo"
            _bench_capture = None
            if _capture_spo:
                try:
                    from dagster_io.bench import spo_capture as _bench_capture  # noqa: PLC0415
                except Exception:
                    _bench_capture = None

            if _bench_capture is not None:
                _capture_cm = _bench_capture.open_capture()
            else:
                # Null-context placeholder so the ``with`` shape stays
                # uniform; we just don't have a real slot to read back.
                import contextlib as _ctx  # noqa: PLC0415

                _capture_cm = _ctx.nullcontext(None)

            with _capture_cm as _capture_slot:
                result = await self.client.structured_output(
                    self.config.extraction_schema,
                    [SystemMessage(content=system), HumanMessage(content=prompt)],
                )
                # Snapshot the slot fields BEFORE the context exits — the
                # contextmanager clears thread-local state on exit but
                # the dataclass instance lives on so attribute access is
                # safe in either order; explicit copy keeps intent clear.
                _captured_raw = getattr(_capture_slot, "raw_text", "") if _capture_slot is not None else ""
                _captured_usage = dict(getattr(_capture_slot, "usage", {}) or {}) if _capture_slot is not None else {}
                _captured_parse_err = (
                    getattr(_capture_slot, "parsing_error", None) if _capture_slot is not None else None
                )

            # Extract candidates from the Pydantic result
            # Works for both MentionExtractionResult.mentions and PropositionExtractionResult.propositions
            candidates = []
            for field_name in ("mentions", "propositions"):
                items = getattr(result, field_name, None)
                if items is not None:
                    candidates = [item.model_dump() for item in items]
                    break

            candidates = correct_candidate_spans(candidates, raw_text)

            # SPO-only: classify parse errors, hash the prompt, compute
            # cost, fire off S3 archive writes, and stash the inline
            # preview fields for emit_chunk_extracted to pick up.
            _spo_capture_details: dict[str, Any] = {}
            if _capture_spo and chunk_id:
                rendered_prompt = system + "\n\n" + prompt
                prompt_hash = _hash_prompt(system, prompt)
                parse_errors = _classify_parse_errors(_captured_parse_err, candidates, _captured_raw)
                # Cost lookup is keyed on the configured model (LLM_MODEL),
                # not the bench display name — the rate table speaks model ids.
                model_id = getattr(self.client, "model", None) or os.environ.get("LLM_MODEL", "")
                cost_usd: float | None = None
                if _captured_usage:
                    try:
                        from dagster_io.bench.llm_costs import compute_cost_usd  # noqa: PLC0415

                        cost_usd = compute_cost_usd(
                            model_id,
                            _captured_usage.get("tokens_in", 0),
                            _captured_usage.get("tokens_out", 0),
                        )
                    except Exception:
                        cost_usd = None

                _spo_capture_details = {
                    "prompt_hash": prompt_hash,
                    "prompt_preview": rendered_prompt[:_SPO_PROMPT_PREVIEW_CHARS],
                    "response_preview": _truncate_response_preview(_captured_raw),
                    "usage": _captured_usage,
                    "cost_usd": cost_usd,
                    "parse_errors": parse_errors,
                }

                # S3 archive of the full prompt + raw response, dispatched
                # to a tiny background pool so we don't block the SPO loop
                # on MinIO latency. Best-effort: failures log and move on.
                run_id = event_store.current_run_id() if hasattr(event_store, "current_run_id") else None
                if run_id:
                    try:
                        executor = _get_spo_s3_executor()
                        executor.submit(
                            _archive_prompt_and_response,
                            prompt_hash,
                            rendered_prompt,
                            run_id,
                            chunk_id,
                            _captured_raw,
                        )
                    except Exception as e:  # noqa: BLE001 — never fail SPO on archive
                        logger.warning("spo-archive submit failed: %s", e)

                # Stash on the module-global buffer; the doc-level SPO
                # orchestrator drains via ``consume_spo_capture(chunk_id)``
                # right before emitting the chunk_extracted event.
                _SPO_CAPTURE_BUFFER[chunk_id] = _spo_capture_details

            elapsed = time.perf_counter() - t0
            logger.info("%s: done, candidates=%d, duration=%.3fs", node_name, len(candidates), elapsed)

            # Initialize or update stage state
            stages = dict(state.get("stages", {}))
            stages[stage_name] = {
                "candidates": candidates,
                "accepted": [],
                "validation": {},
                "retry_count": 0,
                "status": "validating",
                "error": "",
            }

            # If no candidates extracted (e.g. encoder returning empty SPO),
            # skip validation and accept empty list
            if not candidates:
                logger.info("%s: 0 candidates, skipping validation", node_name)
                stages[stage_name]["status"] = "completed"
                stages[stage_name]["accepted"] = []
                return {
                    "stages": stages,
                    "status": ExGraphStatus.COMPLETED.value
                    if stage_name == state.get("_final_stage")
                    else state.get("status", ExGraphStatus.EXTRACTING.value),
                    "audit_events": state.get("audit_events", [])
                    + [
                        make_audit_event(
                            node_name,
                            "completed",
                            state=state,
                            duration_s=elapsed,
                            candidate_count=0,
                            skipped="empty",
                        )
                    ],
                }

            return {
                "stages": stages,
                "status": ExGraphStatus.VALIDATING.value,
                "audit_events": state.get("audit_events", [])
                + [
                    make_audit_event(
                        node_name,
                        "completed",
                        state=state,
                        duration_s=elapsed,
                        candidate_count=len(candidates),
                    )
                ],
            }
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.exception("%s failed", node_name)
            stages = dict(state.get("stages", {}))
            stages[stage_name] = {
                "candidates": [],
                "accepted": [],
                "validation": {},
                "retry_count": 0,
                "status": "error",
                "error": str(e),
            }
            return {
                "stages": stages,
                "status": ExGraphStatus.FAILED.value,
                "error": str(e),
                "audit_events": state.get("audit_events", [])
                + [make_audit_event(node_name, "error", state=state, duration_s=elapsed, error=str(e))],
            }
