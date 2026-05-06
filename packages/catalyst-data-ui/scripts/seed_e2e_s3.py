#!/usr/bin/env python3
"""Seed the moto-server S3 mock with every corpus's bench artefacts.

Runs once from Playwright's globalSetup, after moto-server is healthy.
Connects via boto3 with a hardcoded `http://localhost:5000` endpoint
(moto's default test-mode bind), creates the `dagster` bucket, and
uploads each corpus's content under the medallion + bench keys the
real FastAPI handlers expect.

Layout per corpus (rooted at `e2e/fixtures/corpora/<name>/`):
  events.ndjson      → s3://dagster/bench/runs/<run_id>/events.parquet
                       (converted; bench.py reads via DuckDB read_parquet)
  report.json        → s3://dagster/bench/runs/<run_id>/report.json
  ground-truth.json  → s3://dagster/bench/ground-truth/<corpus>.json
                       (the listing endpoint advertises one GT per corpus
                        keyed by name; specs that need GT use the active corpus)

S3-explorer specs need a small medallion tree to navigate. We seed it
from a separate manifest constant below — keeps the corpus dirs clean
(they're "what gets served on /viewer/api/bench/*"; the medallion
tree is "what gets served on /viewer/api/s3/*").

Run-id convention mirrors `corpora.ts`:
    2025-04-01-115500-fixture-<corpus-name>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
CORPORA_DIR = ROOT / "e2e" / "fixtures" / "corpora"

ENDPOINT_URL = os.environ.get("E2E_S3_ENDPOINT", "http://localhost:4566")
BUCKET = os.environ.get("E2E_S3_BUCKET", "dagster")
ACCESS_KEY = os.environ.get("E2E_S3_ACCESS_KEY", "test")
SECRET_KEY = os.environ.get("E2E_S3_SECRET_KEY", "test")

RUN_ID_PREFIX = "2025-04-01-115500-fixture-"

# Synthetic medallion tree for /viewer/api/s3/* tests. The keys mirror the
# real catalyst-data layout (bronze/silver/gold/bench/dev) so URL params
# the s3-explorer spec hard-codes still resolve.
MEDALLION_TREE: list[tuple[str, bytes, str]] = [
    # (key, content, content_type)
    ("bronze/media/raw/audio_001.wav", b"\x00" * 64, "audio/wav"),
    ("bronze/media/raw/audio_002.wav", b"\x00" * 64, "audio/wav"),
    ("bronze/congress/raw/bill_h1234.xml", b"<bill/>", "application/xml"),
    ("silver/media/transcripts/audio_001.json", b'{"stub": true}', "application/json"),
    (
        "silver/media_ingest/media/media_documents/data.jsonl",
        b'{"id":"doc-001","title":"Sample document","source":"fixture"}\n'
        b'{"id":"doc-002","title":"Second sample","source":"fixture"}',
        "application/x-ndjson",
    ),
    (
        "silver/media_ingest/media/media_chunks/audio_001/data.jsonl",
        b'{"chunk_id":"audio_001:c0","text":"chunk 0"}\n{"chunk_id":"audio_001:c1","text":"chunk 1"}',
        "application/x-ndjson",
    ),
    (
        "gold/media_ingest/media/media_mentions/audio_001/data.jsonl",
        b'{"mention":"Entity0_0","type":"PERSON"}',
        "application/x-ndjson",
    ),
    # 1×1 transparent PNG, audio + video stubs for media-preview tests.
    (
        "dev/fixtures/sample.png",
        bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
            "0000000B49444154789C636001000000050001A5F645400000000049454E44AE426082"
        ),
        "image/png",
    ),
    ("dev/fixtures/sample.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav"),
    ("dev/fixtures/sample.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4"),
]


def _ndjson_to_parquet_bytes(events_path: Path) -> bytes:
    """Convert events.ndjson → events.parquet bytes via PyArrow.

    The real bench events schema is wide and varies per node; for fixture
    purposes we keep all fields nullable and let PyArrow infer types from
    the data. The bench routes read via DuckDB which is permissive about
    schema drift, so a faithful column-by-column inference is sufficient.
    """
    rows: list[dict] = []
    with events_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if not rows:
        # Empty parquet — still valid, bench routes return [] gracefully.
        table = pa.Table.from_pylist([{"run_id": "", "seq": 0, "node_name": "", "ts": ""}], preserve_index=False).slice(
            0, 0
        )
    else:
        # Hoist run_id into every row (bench.py expects this column for
        # the live-vs-archived probe). Derive from the corpus name later
        # in seed_corpus(); for now leave to the caller.
        table = pa.Table.from_pylist(rows)
    buf = pa.BufferOutputStream()
    pq.write_table(table, buf)
    return buf.getvalue().to_pybytes()


def _events_with_run_id(events_path: Path, run_id: str) -> bytes:
    """Convert events.ndjson → parquet bytes, injecting the columns the
    real bench routes' SQL requires.

    Required columns the SPA queries by:
      run_id        — bench.py WHERE clause filters by it
      writer_pid    — ORDER BY seq, writer_pid (multi-writer tiebreak)
      seq           — primary order key

    Plus whatever the original event payload had. Defaults are filled
    only when missing, so a corpus event with a real writer_pid wins.
    """
    rows: list[dict] = []
    with events_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            row.setdefault("run_id", run_id)
            row.setdefault("writer_pid", 1)
            rows.append(row)
    if not rows:
        table = pa.Table.from_pylist(
            [{"run_id": run_id, "seq": 0, "writer_pid": 1, "node_name": "", "ts": ""}],
            preserve_index=False,
        ).slice(0, 0)
    else:
        table = pa.Table.from_pylist(rows)
    buf = pa.BufferOutputStream()
    pq.write_table(table, buf)
    return buf.getvalue().to_pybytes()


def _client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT_URL,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name="us-east-1",
    )


def _ensure_bucket(client) -> None:
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if BUCKET not in existing:
        client.create_bucket(Bucket=BUCKET)


def _put_run(client, run_id: str, report: Path | None, events: Path | None) -> None:
    if report and report.exists():
        # The real backend's S3RunStore.report_key is
        # "<run_prefix>/benchmark-report.json", NOT report.json — match it.
        client.put_object(
            Bucket=BUCKET,
            Key=f"bench/runs/{run_id}/benchmark-report.json",
            Body=report.read_bytes(),
            ContentType="application/json",
        )
    if events and events.exists():
        client.put_object(
            Bucket=BUCKET,
            Key=f"bench/runs/{run_id}/events.parquet",
            Body=_events_with_run_id(events, run_id),
            ContentType="application/octet-stream",
        )


def _seed_chunks_assets(client, events_path: Path) -> None:
    """Walk chunk_loaded events and emit per-doc media_chunks data.jsonl
    so /viewer/api/docs/<doc>/text can resolve via the real chunks-asset
    code path. Each chunk is one row keyed by document_id + index.
    """
    by_doc: dict[str, list[dict]] = {}
    with events_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("node_name") != "chunk_loaded":
                continue
            doc = ev.get("doc_id")
            if not doc:
                continue
            details = ev.get("details") or {}
            by_doc.setdefault(doc, []).append(
                {
                    "document_id": doc,
                    "chunk_id": ev.get("chunk_id"),
                    "index": ev.get("chunk_idx", len(by_doc[doc]) if doc in by_doc else 0),
                    "total_chunks": None,  # patched below
                    "text": details.get("text", ""),
                    "metadata": {},
                }
            )
    for doc, rows in by_doc.items():
        for r in rows:
            r["total_chunks"] = len(rows)
        body = "\n".join(json.dumps(r, separators=(",", ":")) for r in rows).encode("utf-8")
        client.put_object(
            Bucket=BUCKET,
            Key=f"silver/media_ingest/media/media_chunks/{doc}/data.jsonl",
            Body=body,
            ContentType="application/x-ndjson",
        )


def seed_corpus(client, corpus_dir: Path) -> None:
    name = corpus_dir.name

    # Two corpus shapes:
    #  - flat: events.ndjson + report.json at root → one run, id = fixture-<name>
    #  - multi-run: runs/<timestamp>/{events.ndjson,report.json} → N runs, ids = the timestamps
    runs_subdir = corpus_dir / "runs"
    if runs_subdir.is_dir():
        run_dirs = sorted(d for d in runs_subdir.iterdir() if d.is_dir())
        for d in run_dirs:
            _put_run(client, d.name, d / "report.json", d / "events.ndjson")
        # Multi-run corpora share a single doc across runs — seed chunks
        # from the first run's events.
        if run_dirs:
            _seed_chunks_assets(client, run_dirs[0] / "events.ndjson")
        print(f"[seed] {name}: {len(run_dirs)} runs (multi)")
    else:
        run_id = f"{RUN_ID_PREFIX}{name}"
        _put_run(client, run_id, corpus_dir / "report.json", corpus_dir / "events.ndjson")
        events_file = corpus_dir / "events.ndjson"
        if events_file.exists():
            _seed_chunks_assets(client, events_file)
        print(f"[seed] {name}: 1 run (flat) run_id={run_id}")

    # ground-truth.json → bench/ground-truth/active.json — only one active
    # GT at a time; the "happy-path" corpus sets it.
    gt = corpus_dir / "ground-truth.json"
    if gt.exists():
        client.put_object(
            Bucket=BUCKET,
            Key="bench/ground-truth/active.json",
            Body=gt.read_bytes(),
            ContentType="application/json",
        )


def seed_medallion(client) -> None:
    for key, body, ct in MEDALLION_TREE:
        client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=ct)
    print(f"[seed] medallion: {len(MEDALLION_TREE)} keys")


def main() -> int:
    if not CORPORA_DIR.is_dir():
        print(f"[seed] corpora dir missing: {CORPORA_DIR}", file=sys.stderr)
        return 1
    client = _client()
    _ensure_bucket(client)

    corpora = sorted(d for d in CORPORA_DIR.iterdir() if d.is_dir())
    for corpus in corpora:
        seed_corpus(client, corpus)
    seed_medallion(client)

    print(f"[seed] complete — bucket={BUCKET} endpoint={ENDPOINT_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
