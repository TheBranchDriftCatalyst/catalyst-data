"""Document text route — serves the full raw text for a given doc_id by
streaming directly from the medallion silver layer.

Replaces the prior approach of inlining doc text into chunk_loaded audit
events (which was capped at 4 KiB by event_store.emit_chunk_text and
truncated long documents). Audit events now carry only metadata
(char_count, chunk_index, domain, ...); the State Inspector's
DocumentSourcePanel calls ``GET /viewer/api/docs/{doc_id}/text`` for the
canonical doc text on demand.

Architecture:

  StateInspectorV2 → DocumentSourcePanel
        │
        │ GET /viewer/api/docs/{doc_id}/text
        ▼
  this route ──── lists silver/<code_loc>/<group>/<asset>/[<partition>/]data.jsonl
                  for any *_chunks asset across all domains, filters by
                  document_id, sorts by chunk_index, concatenates text.

Cache: TTL'd in-process so repeated panel opens for the same doc don't
re-walk silver every time. Bench runs are mostly read-only against a
stable silver layer, so a 5-minute TTL is generous.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)

router = APIRouter(prefix="/viewer/api/docs", tags=["docs"])

# Mirrors the regex in tests/shared/medallion.py — chunks-asset key shape:
#   <layer>/<code_loc>/<group>/<asset>/[<partition>/]data.jsonl
_KEY_RE = re.compile(
    r"^(?P<layer>silver|gold)/"
    r"(?P<code_loc>[^/]+)/"
    r"(?P<group>[^/]+)/"
    r"(?P<asset>[^/]*chunks)/"
    r"(?:(?P<partition>[^/]+)/)?"
    r"data\.jsonl$"
)

# Doc-type-aware silver paths (in priority order — first hit wins).
# Each entry is a (description, key_template) pair. doc_id is substituted
# in via str.format(doc_id=...). Templates with {doc_id} expand once;
# templates without it are listed and filtered by document_id field.
#
# Why these specific paths: the *_chunks asset is the canonical source
# for chunked NER input but for video docs the segment-merged transcript
# (media_segment_merge) is closer to the operator's mental model of "the
# doc text" — chunks are a lossy rebuild because chunk_overlap drops some
# text and the inline speaker tags / windowing reshape the original.
# Legal docs (congress bills, leak cables) are inverse: chunks ARE the
# canonical text since the document IS chunked text, no upstream
# transcript stage exists.
_DOC_SOURCE_PATHS: tuple[tuple[str, str], ...] = (
    # Media: prefer the segment-merged transcript over chunks for fidelity.
    ("media_segment_merge", "silver/media_ingest/media/media_segment_merge/{doc_id}/data.jsonl"),
    # Congress + leaks: bill_documents / leak_documents carry the canonical
    # text upstream of chunking. These are dagster assets that emit one
    # row per doc with .content or .text.
    ("congress_bill_documents", "silver/congress_data/bills/bill_documents/{doc_id}/data.jsonl"),
    ("open_leak_documents", "silver/open_leaks/leaks/leak_documents/data.jsonl"),
)


def _read_text_from_doc_asset(client: S3Client, key: str, doc_id: str) -> str | None:
    """Try to read full doc text from a non-chunks asset (e.g. media_segment_merge,
    bill_documents, leak_documents). Each row is one doc; for partitioned
    assets the key already targets the doc, for unpartitioned assets we
    filter by document_id.

    Returns the concatenated text or ``None`` when the key doesn't exist
    or the doc isn't in it.
    """
    try:
        rows = _read_jsonl(client, key)
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None

    # If the key is doc-partitioned (key contains the doc_id) we just read
    # rows directly. For unpartitioned assets (open_leaks) filter by
    # document_id.
    matching = [r for r in rows if not doc_id or r.get("document_id") in (doc_id, None) or r.get("id") == doc_id]
    # If we filtered to empty, fall back to reading all rows when the path
    # already targeted the doc (partitioned case).
    if not matching and f"/{doc_id}/" in key:
        matching = rows
    if not matching:
        return None

    # Media segment-merge: rows have .segments[] with per-turn .text. Other
    # asset shapes vary — try the most likely fields in order.
    for row in matching:
        if isinstance(row.get("segments"), list):
            segs = row["segments"]
            return "\n\n".join((s.get("text") or "") for s in segs if s.get("text"))
        if "text" in row and row["text"]:
            return str(row["text"])
        if "content" in row and row["content"]:
            return str(row["content"])
    return None


_client: S3Client | None = None


def _s3() -> S3Client:
    global _client
    if _client is None:
        _client = S3Client(
            endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
            access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
            secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
            bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
        )
    return _client


# In-process cache: doc_id → (timestamp, payload). Stable silver means
# 5min TTL is plenty; user clicks across docs just walk the cache.
_TEXT_TTL_SECONDS = 300.0
_text_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_text_lock = threading.Lock()


def _list_chunk_keys(client: S3Client) -> list[str]:
    """Return every *_chunks asset's data.jsonl key under silver/ + gold/."""
    keys: list[str] = []
    for layer in ("silver", "gold"):
        for key in client.list_all_objects(f"{layer}/"):
            if _KEY_RE.match(key):
                keys.append(key)
    return keys


def _read_jsonl(client: S3Client, key: str) -> list[dict[str, Any]]:
    """Read a JSONL object as a list of dicts, dropping unparseable lines."""
    raw = client.get_object(key)
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _resolve_doc(doc_id: str) -> dict[str, Any] | None:
    """Resolve the doc payload — full text + chunk-range overlay.

    The viewer renders the full text once and overlays chunk boundaries
    on top so the operator can see *where each chunk begins/ends within
    the original text*. To do that the response carries:

      {
        "doc_id": ...,
        "source": "media_segment_merge" | "bill_documents" | "chunks_concat",
        "text": "<full doc text>",
        "char_count": ...,
        "chunks": [
          {"chunk_id": ..., "index": ..., "start": <char offset in text>,
           "end": <char offset>, "text_preview": <first 100 chars>,
           "metadata": {speaker, temporal_*, strategy, ...}},
          ...
        ]
      }

    Strategy:

    1. Walk every *_chunks asset, gather rows by document_id, sort by
       index. This is the "concatenated chunks" view and gives us the
       exact text the encoders saw — the right thing to render when the
       user clicks the document node in the pipeline graph. Each chunk
       is one block separated by ``\\n\\n``; chunk start = running cursor,
       chunk end = cursor + len(chunk text).
    2. Doc-type-aware fallback (media transcript, bill/leak documents)
       used only when (1) returns no chunks. Yields a single chunk
       spanning the whole doc (no boundaries to draw, but better than
       nothing).

    Returns ``None`` when nothing matches.
    """
    client = _s3()

    # 1. Chunks-asset path — gives us full-fidelity boundaries.
    matching: list[dict[str, Any]] = []
    for key in _list_chunk_keys(client):
        for row in _read_jsonl(client, key):
            if row.get("document_id") == doc_id:
                matching.append(row)

    if matching:
        matching.sort(key=lambda r: r.get("index") if r.get("index") is not None else 0)
        sep = "\n\n"
        parts: list[str] = []
        chunks_payload: list[dict[str, Any]] = []
        cursor = 0
        for row in matching:
            text = (row.get("text") or "").rstrip()
            start = cursor
            end = cursor + len(text)
            preview = text[:120].replace("\n", " ")
            chunks_payload.append(
                {
                    "chunk_id": row.get("chunk_id"),
                    "index": row.get("index"),
                    "total_chunks": row.get("total_chunks"),
                    "start": start,
                    "end": end,
                    "char_count": len(text),
                    "text_preview": preview,
                    "metadata": row.get("metadata") or {},
                }
            )
            parts.append(text)
            cursor = end + len(sep)
        full_text = sep.join(parts)
        return {
            "doc_id": doc_id,
            "source": "chunks_concat",
            "text": full_text,
            "char_count": len(full_text),
            "chunks": chunks_payload,
        }

    # 2. Fallback: pull from doc-type-aware silver paths. Single chunk
    # spanning the whole text (no boundary overlay since the source isn't
    # chunked).
    for label, template in _DOC_SOURCE_PATHS:
        key = template.format(doc_id=doc_id) if "{doc_id}" in template else template
        text = _read_text_from_doc_asset(client, key, doc_id)
        if text:
            return {
                "doc_id": doc_id,
                "source": label,
                "text": text,
                "char_count": len(text),
                "chunks": [
                    {
                        "chunk_id": doc_id,
                        "index": 0,
                        "total_chunks": 1,
                        "start": 0,
                        "end": len(text),
                        "char_count": len(text),
                        "text_preview": text[:120].replace("\n", " "),
                        "metadata": {"strategy": label},
                    }
                ],
            }

    return None


@router.get("/{doc_id}/text")
def doc_text(doc_id: str) -> dict[str, Any]:
    """Return the full raw text for ``doc_id`` plus per-chunk char ranges
    so the State Inspector can render the entire doc with chunk boundaries
    visually overlaid.

    Response shape::

        {
          "doc_id": "<id>",
          "source": "chunks_concat" | "media_segment_merge" | "<asset-label>",
          "text": "<full doc text>",
          "char_count": <int>,
          "chunks": [
            {"chunk_id": ..., "index": ..., "total_chunks": ...,
             "start": <int>, "end": <int>, "char_count": <int>,
             "text_preview": "<first 120 chars>", "metadata": {...}},
            ...
          ]
        }

    DocumentSourcePanel walks ``chunks`` to slice ``text`` into segments
    and draw boundary markers / hover tooltips per chunk.

    Source preference (in order):
    1. ``chunks_concat`` — concatenate every chunks-asset row matching
       this doc_id in chunk_index order. Authoritative range data.
    2. Doc-type-aware sources (``media_segment_merge`` for video docs,
       ``bill_documents`` / ``leak_documents`` for legal docs). Returned
       as a single chunk spanning the full text — no per-chunk overlay
       since the source upstream of chunking has no chunk concept.

    404 when the doc has no rows in any silver source — typically means
    the silver layer hasn't been materialized for that doc yet. Run
    ``task seed:<domain>`` or the equivalent chunks-regen task.
    """
    if not doc_id or "/" in doc_id:
        raise HTTPException(status_code=400, detail=f"invalid doc_id: {doc_id!r}")

    now = time.time()
    with _text_lock:
        cached = _text_cache.get(doc_id)
        if cached and now - cached[0] < _TEXT_TTL_SECONDS:
            return cached[1]

    payload = _resolve_doc(doc_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"no chunks found for doc_id={doc_id!r} — has silver been materialized?",
        )

    with _text_lock:
        _text_cache[doc_id] = (now, payload)

    return payload
