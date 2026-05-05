"""Read Dagster ``*_chunks`` asset outputs from the medallion S3 bucket.

Each domain's chunks asset writes to the canonical medallion path that
``MinioIOManager`` produces:

    <layer>/<code_loc>/<group>/<asset>/[<partition>/]data.jsonl

This helper lists the bucket and merges chunks across all domains. After
Phase 4 there is no local-disk variant — the local Tilt-managed MinIO
container is the dev backend; the cluster Tenant is prod. Same S3 surface
either way, controlled by ``DAGSTER_S3_ENDPOINT_URL``.

On a fresh checkout, run ``task seed`` (or just ``task seed:<domain>``)
to populate the chunks before invoking the bench harness.
"""

from __future__ import annotations

import json
import os
import re

# Patterns that match a chunks asset's data.jsonl key. Layer is per-asset
# (media_chunks=gold; bill_chunks/leak_chunks/congress_chunks=silver), and
# partition state varies by domain (media + congress are partitioned per
# doc_id/bill_id, open-leaks is unpartitioned).
_KEY_RE = re.compile(
    r"^(?P<layer>silver|gold)/"
    r"(?P<code_loc>[^/]+)/"
    r"(?P<group>[^/]+)/"
    r"(?P<asset>[^/]*chunks)/"
    r"(?:(?P<partition>[^/]+)/)?"
    r"data\.jsonl$"
)


def _build_client():
    """Same client wiring as the rest of the post-Phase-4 dev tooling.
    DAGSTER_S3_ENDPOINT_URL points at localhost:9000 in dev (set by
    .envrc), the cluster Tenant in prod-ops mode."""
    from dagster_io.s3_client import S3Client

    return S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )


def _list_chunk_keys(client) -> dict[str, list[str]]:
    """Return ``{code_location: [keys]}`` for every chunks-asset data file."""
    by_code_loc: dict[str, list[str]] = {}
    # Two top-level prefixes — silver and gold — cover all chunks assets.
    for layer in ("silver", "gold"):
        for key in client.list_all_objects(f"{layer}/"):
            m = _KEY_RE.match(key)
            if not m:
                continue
            by_code_loc.setdefault(m.group("code_loc"), []).append(key)
    return by_code_loc


def _read_metadata_sidecar(client, data_jsonl_key: str) -> dict | None:
    """Best-effort read of the ``_metadata.json`` sidecar next to a chunks
    ``data.jsonl`` key. Returns ``None`` on any miss / decode failure so a
    legacy materialization without a sidecar (or one removed by a manual
    bucket cleanup) doesn't kill the bench run.

    The IOManager (``MinioIOManager._build_metadata``) writes the sidecar
    in lockstep with each ``data.jsonl``, so on a healthy bucket every
    listed data file has one. CD-d7tb relies on the sidecar's ``run_id``
    + ``upstream_assets`` + ``timestamp`` to wire the harness ``asset_read``
    events back to the Dagster materialization that produced them.
    """
    if not data_jsonl_key.endswith("data.jsonl"):
        return None
    sidecar_key = data_jsonl_key[: -len("data.jsonl")] + "_metadata.json"
    try:
        raw = client.get_object(sidecar_key)
    except Exception:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None


def _emit_asset_read_events(client, keys_by_domain: dict[str, list[str]]) -> None:
    """Emit one ``source=harness, node_name=asset_read`` bench event per
    chunk file the harness is about to read (CD-d7tb).

    Pairs with the Dagster-side ``asset_materialized`` events emitted by
    ``_emit_asset_materialization_events`` (CD-7pr0): the harness side
    closes the loop so StateInspector can trace
    "this consensus event came from chunks materialized by run abc123".

    Skipped silently when ``event_store`` is unconfigured — unit tests
    and one-off scripts that import ``load_chunks`` outside a bench run
    aren't forced to wire the writer up.
    """
    from dagster_io.bench import event_store

    if not event_store.is_configured():
        return

    bucket = os.environ.get("DAGSTER_S3_BUCKET", "dagster")
    for _domain, keys in keys_by_domain.items():
        for key in sorted(keys):
            m = _KEY_RE.match(key)
            if m is None:
                continue
            sidecar = _read_metadata_sidecar(client, key) or {}
            partition_key = m.group("partition")
            event_store.append(
                source="harness",
                node_name="asset_read",
                status="ok",
                code_location=m.group("code_loc"),
                # ``doc_id`` is the partition key for partitioned assets;
                # unpartitioned reads (open-leaks) land in ``__run__``
                # alongside the run-level summary.
                doc_id=partition_key,
                details={
                    "asset_key": sidecar.get("asset_key") or m.group("asset"),
                    "partition_key": partition_key,
                    "dagster_run_id": sidecar.get("run_id"),
                    "layer": m.group("layer"),
                    "output_path": f"s3://{bucket}/{key}",
                    "row_count": sidecar.get("count"),
                    "size_bytes": sidecar.get("size_bytes"),
                    "schema": sidecar.get("schema"),
                    "upstream_assets": sidecar.get("upstream_assets") or [],
                    "materialized_at": sidecar.get("timestamp"),
                },
            )


def load_chunks(
    doc_ids: list[str] | None = None,
    sample_per_domain: int | None = None,
) -> list[dict]:
    """Load chunks from any ``*_chunks`` asset across all domains.

    Args:
        doc_ids: Filter by each chunk's ``document_id`` field. Works
            uniformly for partitioned and unpartitioned outputs.
        sample_per_domain: Cap rows per domain (key-based, distributes
            the cap evenly across partition files via round-robin).
            Critical for benchmarks: open-leaks's 3.6M chunks across one
            file or media-ingest's chunks across 7 doc_ids would
            otherwise sample lopsidedly. Default ``None`` (no cap).

    Returns ``[]`` if nothing has been materialized yet.
    """
    client = _build_client()
    keys_by_domain = _list_chunk_keys(client)

    # Emit ``asset_read`` events for every key we'll read at this call,
    # before any sample_per_domain truncation kicks in — a sampled run
    # still touches every key (round-robin) so all upstream lineage
    # belongs in the audit log.
    _emit_asset_read_events(client, keys_by_domain)

    merged: list[dict] = []
    for _domain, keys in keys_by_domain.items():
        keys_sorted = sorted(keys)
        if sample_per_domain is None:
            for key in keys_sorted:
                merged.extend(_read_jsonl(client, key, doc_ids))
            continue

        # Round-robin: pull from each key iteratively until the per-domain
        # cap is met. Each key gets ~ceil(cap / num_keys) rows; if a small
        # key runs out early the remaining cap rolls over to the others.
        per_file_quota = max(1, -(-sample_per_domain // len(keys_sorted)))
        per_domain_count = 0
        iters = [_iter_jsonl(client, k, doc_ids) for k in keys_sorted]
        active = list(range(len(iters)))
        per_file_taken = [0] * len(iters)

        while active and per_domain_count < sample_per_domain:
            next_active = []
            for i in active:
                if per_domain_count >= sample_per_domain:
                    break
                if per_file_taken[i] >= per_file_quota:
                    next_active.append(i)
                    continue
                try:
                    merged.append(next(iters[i]))
                    per_file_taken[i] += 1
                    per_domain_count += 1
                    next_active.append(i)
                except StopIteration:
                    pass
            if all(per_file_taken[i] >= per_file_quota for i in next_active):
                per_file_quota += 1
            active = next_active

    return merged


def load_doc_texts(
    doc_ids: list[str] | None = None,
    sample_per_domain: int | None = None,
) -> list[dict]:
    """Load full doc texts by concatenating chunks per doc_id in index order.

    Returns a list of dicts:
    ``{doc_id, full_text, code_location, domain, chunks: [...]}``
    where ``chunks`` are the original chunk dicts in index order.

    Args:
        doc_ids: Optional filter — same semantics as ``load_chunks(doc_ids=...)``.
        sample_per_domain: Cap chunks per domain before grouping (same as
            ``load_chunks(sample_per_domain=...)``, applied before grouping
            so heavy domains don't dominate the doc list).

    Returns ``[]`` if nothing has been materialized yet.
    """
    from collections import defaultdict

    raw_chunks = load_chunks(doc_ids=doc_ids, sample_per_domain=sample_per_domain)
    if not raw_chunks:
        return []

    # Group by document_id, preserving insertion order for stable iteration.
    groups: dict[str, list[dict]] = defaultdict(list)
    for chunk in raw_chunks:
        did = chunk.get("document_id") or "unknown"
        groups[did].append(chunk)

    docs: list[dict] = []
    for did, chunks in groups.items():
        # Sort by chunk index so concatenation is deterministic.
        sorted_chunks = sorted(chunks, key=lambda c: c.get("index", 0))
        full_text = "\n\n".join(c.get("text", "") or "" for c in sorted_chunks)
        first_meta = sorted_chunks[0].get("metadata") or {}
        docs.append(
            {
                "doc_id": did,
                "full_text": full_text,
                "code_location": first_meta.get("code_location") or first_meta.get("source") or "",
                "domain": first_meta.get("domain") or first_meta.get("source") or "",
                "chunks": sorted_chunks,
            }
        )

    return docs


def _read_jsonl(client, key: str, doc_ids: list[str] | None) -> list[dict]:
    try:
        raw = client.get_object(key)
    except Exception:
        return []
    rows: list[dict] = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if doc_ids and row.get("document_id") not in doc_ids:
            continue
        rows.append(row)
    return rows


def _iter_jsonl(client, key: str, doc_ids: list[str] | None):
    """Yield chunk dicts one at a time; respects doc_ids filter."""
    try:
        raw = client.get_object(key)
    except Exception:
        return
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if doc_ids and row.get("document_id") not in doc_ids:
            continue
        yield row
