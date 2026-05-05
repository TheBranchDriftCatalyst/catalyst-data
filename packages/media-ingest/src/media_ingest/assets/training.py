"""Training-dataset Dagster assets — SFT + DPO JSONL outputs in S3.

These assets read the silver+gold medallion artifacts plus the active
``bench/ground-truth/active.json`` and emit JSONL training corpora to
``s3://<bucket>/bench/training/sft/<domain>/data.jsonl`` and
``.../dpo/<domain>/data.jsonl``. An off-cluster fine-tuning script
(``scripts/training/pull_training_dataset.py``, Phase 3.3) downloads the JSONL
to a GPU box without re-running this asset.

Hybrid-(c) ground truth: where a chunk's per-chunk ``reviewed`` flag is
true in the GT file, the SFT row uses the human-curated mentions and
propositions; otherwise it uses ensemble consensus from the gold layer
(media_mentions + media_assertions). The file-level
``manually_reviewed`` flag is informational only — the per-chunk flag
is what gates the human/ensemble swap.

S3 input keys (medallion convention, see
``packages/media-ingest/src/media_ingest/viewer/services/s3_data.py``):
  silver/media_ingest/media/media_documents/data.jsonl       — doc list
  gold/media_ingest/media/media_chunks/<doc_id>/data.jsonl   — chunk text
  gold/media_ingest/media/media_mentions/<doc_id>/data.jsonl — gold mentions
  gold/media_ingest/media/media_assertions/<doc_id>/data.jsonl — gold assertions
"""

import json
import os
from datetime import UTC, datetime
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.bench import S3BenchmarkStore
from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)

_DOMAIN = "media_ingest"
_GROUP = "media"


def _silver_documents_key() -> str:
    return f"silver/{_DOMAIN}/{_GROUP}/media_documents/data.jsonl"


def _gold_key(asset_name: str, doc_id: str, ext: str = "jsonl") -> str:
    return f"gold/{_DOMAIN}/{_GROUP}/{asset_name}/{doc_id}/data.{ext}"


def _media_s3_client() -> S3Client:
    """Same client config as the viewer's S3 explorer routes — talks to the
    Tilt-managed local MinIO in dev and the cluster Tenant in prod."""
    return S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )


def _try_load_jsonl(client: S3Client, key: str) -> list[dict[str, Any]]:
    """Best-effort JSONL fetch — returns [] if the key is missing."""
    try:
        raw = client.get_object(key)
    except Exception:
        return []
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def _index_by_chunk_id(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group items by ``provenance.chunk_id`` (gold mentions/assertions carry
    full provenance). Falls back to a top-level ``chunk_id`` field if present.
    Items missing both are dropped — they can't be joined to a chunk."""
    by_chunk: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        chunk_id = (item.get("provenance") or {}).get("chunk_id") or item.get("chunk_id")
        if not chunk_id:
            continue
        by_chunk.setdefault(chunk_id, []).append(item)
    return by_chunk


@asset(
    name="sft_dataset",
    group_name="media",
    description=(
        "SFT training JSONL emitted to s3://<bucket>/bench/training/sft/<domain>/data.jsonl. "
        "One row per chunk: chunk_text, accepted mentions/propositions, hybrid-(c) GT swap "
        "for chunks marked manually-reviewed."
    ),
    compute_kind="s3",
)
def sft_dataset(context: AssetExecutionContext) -> Output[dict[str, Any]]:
    media_client = _media_s3_client()
    bench = S3BenchmarkStore()

    # ── Document discovery: read the silver-layer media_documents list.
    docs = _try_load_jsonl(media_client, _silver_documents_key())
    doc_ids = [d.get("id") for d in docs if d.get("id")]
    context.log.info("Discovered %d documents in silver/media_documents", len(doc_ids))

    # ── Active ground truth (hybrid-(c) source for reviewed chunks).
    gt = bench.load_ground_truth("active") or {}
    gt_by_chunk = {c["chunk_id"]: c for c in gt.get("chunks", []) if c.get("chunk_id")}
    file_reviewed = bool(gt.get("manually_reviewed", False))

    # ── Per-doc walk: load chunks + gold mentions + gold assertions; join
    # by chunk_id; emit one SFT row per chunk.
    rows: list[dict[str, Any]] = []
    chunks_used = 0
    chunks_from_gt = 0

    for doc_id in doc_ids:
        chunks = _try_load_jsonl(media_client, _gold_key("media_chunks", doc_id))
        mentions = _try_load_jsonl(media_client, _gold_key("media_mentions", doc_id))
        assertions = _try_load_jsonl(media_client, _gold_key("media_assertions", doc_id))

        if not chunks:
            continue

        m_by_chunk = _index_by_chunk_id(mentions)
        a_by_chunk = _index_by_chunk_id(assertions)

        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            if not chunk_id:
                continue
            chunks_used += 1

            gt_chunk = gt_by_chunk.get(chunk_id)
            chunk_reviewed = bool(gt_chunk and gt_chunk.get("reviewed", False))

            if chunk_reviewed and gt_chunk:
                row_mentions = gt_chunk.get("mentions", [])
                row_propositions = gt_chunk.get("propositions", [])
                source = "human"
                chunks_from_gt += 1
            else:
                row_mentions = m_by_chunk.get(chunk_id, [])
                row_propositions = a_by_chunk.get(chunk_id, [])
                source = "ensemble"

            rows.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": doc_id,
                    "domain": _DOMAIN,
                    "chunk_text": chunk.get("text", ""),
                    "manually_reviewed": chunk_reviewed,
                    "ground_truth_source": source,
                    "mentions": row_mentions,
                    "propositions": row_propositions,
                    "seed": (chunk.get("metadata") or {}).get("semantic_seed"),
                }
            )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "domain": _DOMAIN,
        "row_count": len(rows),
        "chunks_used": chunks_used,
        "chunks_from_human_gt": chunks_from_gt,
        "file_level_reviewed": file_reviewed,
    }

    # ── JSONL emission. Per-domain + an "all" union (which today is just
    # this domain — congress + open-leaks join in once they ship their own
    # training assets).
    jsonl = "\n".join(json.dumps(r, default=str) for r in rows) + ("\n" if rows else "")
    domain_key = f"{bench.training_prefix}/sft/{_DOMAIN}/data.jsonl"
    union_key = f"{bench.training_prefix}/sft/all/data.jsonl"
    bench.client.put_object(domain_key, jsonl.encode("utf-8"))
    bench.client.put_object(union_key, jsonl.encode("utf-8"))

    domain_uri = f"s3://{bench.bucket}/{domain_key}"
    union_uri = f"s3://{bench.bucket}/{union_key}"
    context.log.info(
        "SFT dataset: %d rows (%d from human GT) → %s",
        len(rows),
        chunks_from_gt,
        domain_uri,
    )

    return Output(
        payload,
        metadata={
            "row_count": len(rows),
            "chunks_used": chunks_used,
            "chunks_from_human_gt": chunks_from_gt,
            "domain_uri": MetadataValue.path(domain_uri),
            "union_uri": MetadataValue.path(union_uri),
        },
    )


@asset(
    name="dpo_dataset",
    group_name="media",
    description=(
        "DPO preference-pair JSONL emitted to "
        "s3://<bucket>/bench/training/dpo/<domain>/data.jsonl. One row per "
        "(chunk, preferred_model, rejected_model) — preferred = closest-to-GT "
        "by per-chunk F1, rejected = furthest. Source: bench/runs/<latest>/"
        "extractions/extraction_<model>.json fan-out, scored chunk-by-chunk "
        "against bench/ground-truth/active.json via score_per_chunk()."
    ),
    compute_kind="s3",
)
def dpo_dataset(context: AssetExecutionContext) -> Output[dict[str, Any]]:
    """Build DPO preference pairs from per-chunk model-vs-GT F1 deltas.

    Pipeline:
      1. Find the latest bench run; load every extraction_<model>.json
      2. Load the active GT (bench/ground-truth/active.json)
      3. For each model, call score_per_chunk(predicted_mentions, gt_mentions)
      4. For each chunk: find the model with the highest mention_strict_f1
         (preferred) and the lowest non-empty score (rejected). Skip chunks
         where only one model produced output (no preference pair to form)
         or where preferred == rejected (degenerate).
      5. Emit the preference row with chunk text + preferred/rejected model
         extractions inline so the trainer doesn't need to re-join later.

    HITL reject signal (viewer_entity_overrides.kind=reject) is a future
    extension — once that schema column lands, rejected mention sets get
    augmented with explicit human rejections. Tracked under CD-thpq.
    """
    from tests.shared.extraction_scoring import score_per_chunk

    bench = S3BenchmarkStore()

    # ── Inputs
    gt = bench.load_ground_truth("active") or {}
    gt_chunks = gt.get("chunks", []) or []
    if not gt_chunks:
        context.log.warning("No active ground truth — DPO dataset will be empty.")

    gt_mentions_by_chunk: dict[str, list[dict[str, Any]]] = {}
    gt_propositions_by_chunk: dict[str, list[dict[str, Any]]] = {}
    chunk_texts: dict[str, str] = {}
    for c in gt_chunks:
        cid = c.get("chunk_id")
        if not cid:
            continue
        chunk_texts[cid] = c.get("text", "")
        # Stamp chunk_id onto each mention/prop so score_per_chunk can bucket them.
        for m in c.get("mentions", []) or []:
            m_with_cid = {**m, "chunk_id": cid}
            gt_mentions_by_chunk.setdefault(cid, []).append(m_with_cid)
        for p in c.get("propositions", []) or []:
            p_with_cid = {**p, "chunk_id": cid}
            gt_propositions_by_chunk.setdefault(cid, []).append(p_with_cid)
    flat_gt_mentions = [m for ms in gt_mentions_by_chunk.values() for m in ms]
    flat_gt_propositions = [p for ps in gt_propositions_by_chunk.values() for p in ps]

    latest_run = bench.load_run("latest")
    if latest_run is None:
        context.log.warning("No bench run in S3 — DPO dataset will be empty.")
        models: list[str] = []
    else:
        models = latest_run.list_extractions()
    context.log.info("DPO source: run=%s models=%d", getattr(latest_run, "run_id", None), len(models))

    # ── Score each model chunk-by-chunk
    per_model_scores: dict[str, dict[str, dict[str, float]]] = {}
    per_model_extraction: dict[str, dict[str, Any]] = {}
    for m in models:
        if latest_run is None:
            break
        ext = latest_run.load_extraction(m)
        if not ext:
            continue
        per_model_extraction[m] = ext
        # Stamp chunk_id onto predicted items so score_per_chunk can bucket them.
        pred_mentions: list[dict[str, Any]] = []
        for it in ext.get("mentions", []) or []:
            cid = (it.get("provenance") or {}).get("chunk_id") or it.get("chunk_id")
            if cid:
                pred_mentions.append({**it, "chunk_id": cid})
        pred_propositions: list[dict[str, Any]] = []
        for it in ext.get("assertions", []) or ext.get("propositions", []) or []:
            cid = (it.get("provenance") or {}).get("chunk_id") or it.get("chunk_id")
            if cid:
                pred_propositions.append({**it, "chunk_id": cid})
        per_model_scores[m] = score_per_chunk(pred_mentions, flat_gt_mentions, pred_propositions, flat_gt_propositions)

    # ── Form preference pairs per chunk
    rows: list[dict[str, Any]] = []
    for cid, _gt_chunk in {c.get("chunk_id"): c for c in gt_chunks if c.get("chunk_id")}.items():
        # Per-model F1 on this chunk; default 0.0 if model didn't produce output.
        scored_models = [(m, per_model_scores.get(m, {}).get(cid, {}).get("mention_strict_f1", 0.0)) for m in models]
        scored_models = [s for s in scored_models if s[0] in per_model_extraction]
        if len(scored_models) < 2:
            continue  # need at least 2 models to form a preference pair
        scored_models.sort(key=lambda s: s[1], reverse=True)
        preferred_model, preferred_f1 = scored_models[0]
        rejected_model, rejected_f1 = scored_models[-1]
        if preferred_model == rejected_model or preferred_f1 == rejected_f1:
            continue  # no preference signal

        # Inline the extractions for this chunk so the trainer doesn't re-join.
        def _filter(ext: dict[str, Any], cid: str) -> dict[str, Any]:
            def _matches(it: dict[str, Any]) -> bool:
                return ((it.get("provenance") or {}).get("chunk_id") or it.get("chunk_id")) == cid

            return {
                "mentions": [m for m in ext.get("mentions", []) or [] if _matches(m)],
                "propositions": [
                    p for p in ext.get("assertions", []) or ext.get("propositions", []) or [] if _matches(p)
                ],
            }

        rows.append(
            {
                "chunk_id": cid,
                "chunk_text": chunk_texts.get(cid, ""),
                "preferred": {
                    "model": preferred_model,
                    "f1": round(preferred_f1, 4),
                    "extraction": _filter(per_model_extraction[preferred_model], cid),
                },
                "rejected": {
                    "model": rejected_model,
                    "f1": round(rejected_f1, 4),
                    "extraction": _filter(per_model_extraction[rejected_model], cid),
                },
                "f1_delta": round(preferred_f1 - rejected_f1, 4),
                "source": "model-vs-gt",
            }
        )

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "domain": _DOMAIN,
        "run_id": getattr(latest_run, "run_id", None),
        "models_scored": list(per_model_extraction.keys()),
        "row_count": len(rows),
    }

    jsonl = "\n".join(json.dumps(r, default=str) for r in rows) + ("\n" if rows else "")
    domain_key = f"{bench.training_prefix}/dpo/{_DOMAIN}/data.jsonl"
    bench.client.put_object(domain_key, jsonl.encode("utf-8"))
    domain_uri = f"s3://{bench.bucket}/{domain_key}"

    context.log.info("DPO dataset: %d preference pairs → %s", len(rows), domain_uri)

    return Output(
        payload,
        metadata={
            "row_count": len(rows),
            "models_scored": len(per_model_extraction),
            "domain_uri": MetadataValue.path(domain_uri),
        },
    )
