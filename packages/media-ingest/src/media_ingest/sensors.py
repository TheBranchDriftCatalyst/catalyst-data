"""Sensor that watches media_documents and registers dynamic partitions for new files.

For each new document_id found in media_documents that is not yet a registered
partition, the sensor registers the partition key and yields a RunRequest to
materialize the full partitioned downstream chain:
  media_transcriptions -> media_chunks -> {media_mentions, media_assertions, media_embeddings}
"""

import json
import os

from dagster import (
    AssetKey,
    RunConfig,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    sensor,
)

from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)

# The S3 path where media_documents data lives — the sensor reads this to
# discover new document IDs without needing to run the discovery pipeline.
_DOCUMENTS_S3_PREFIX = "silver/default/media/media_documents"


def _get_s3_client() -> S3Client:
    """Create an S3Client from environment variables."""
    return S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://minio.minio.svc.cluster.local"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )


def _load_document_ids(client: S3Client) -> list[str]:
    """Read the media_documents data.jsonl from S3 and extract document IDs."""
    # Try both .jsonl and .json — the serializer picks format based on data shape
    for ext in (".jsonl", ".json"):
        key = f"{_DOCUMENTS_S3_PREFIX}/data{ext}"
        try:
            payload = client.get_object(key)
            if ext == ".jsonl":
                lines = payload.decode("utf-8").strip().split("\n")
                docs = [json.loads(line) for line in lines if line.strip()]
                return [d["id"] for d in docs if d.get("id")]
            else:
                data = json.loads(payload.decode("utf-8"))
                if isinstance(data, list):
                    return [d["id"] for d in data if d.get("id")]
                elif isinstance(data, dict) and "id" in data:
                    return [data["id"]]
                return []
        except Exception:
            continue
    return []


@sensor(
    name="media_document_sensor",
    description=(
        "Watches media_documents in S3 for new document IDs. "
        "Registers dynamic partitions and triggers transcription+downstream for each new file."
    ),
    minimum_interval_seconds=300,  # Check every 5 minutes
    asset_selection=[
        AssetKey("media_transcriptions"),
        AssetKey("media_chunks"),
        AssetKey("media_mentions"),
        AssetKey("media_assertions"),
        AssetKey("media_embeddings"),
        AssetKey("media_entity_candidates"),
    ],
)
def media_document_sensor(context: SensorEvaluationContext):
    """Detect new media documents and kick off per-document processing."""
    try:
        client = _get_s3_client()
        all_doc_ids = _load_document_ids(client)
    except Exception as e:
        logger.warning("media_document_sensor: failed to load document IDs from S3: %s", e)
        yield SkipReason(f"Failed to read media_documents from S3: {e}")
        return

    if not all_doc_ids:
        yield SkipReason("No documents found in media_documents S3 data")
        return

    # Get currently registered partitions
    existing_partitions = set(
        context.instance.get_dynamic_partitions("media_document")
    )

    # Filter to only audio documents by checking which have already been transcribed
    # We register ALL document IDs as partitions but only create RunRequests for new ones
    new_ids = [doc_id for doc_id in all_doc_ids if doc_id not in existing_partitions]

    if not new_ids:
        yield SkipReason(
            f"All {len(all_doc_ids)} documents already have partitions registered"
        )
        return

    # Register new partition keys
    context.instance.add_dynamic_partitions("media_document", new_ids)
    logger.info(
        "media_document_sensor: registered %d new partitions (total=%d)",
        len(new_ids), len(all_doc_ids),
    )

    # Yield a RunRequest for each new document
    for doc_id in new_ids:
        context.log.info(f"New document detected: {doc_id}")
        yield RunRequest(
            run_key=f"media_document_{doc_id}",
            partition_key=doc_id,
        )

    logger.info(
        "media_document_sensor: yielded %d RunRequests for new documents", len(new_ids)
    )
