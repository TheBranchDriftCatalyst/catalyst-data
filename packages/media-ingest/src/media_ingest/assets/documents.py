"""Stage 3: Transform media metadata into Document objects."""

import os
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset
from pydantic import BaseModel, Field

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation

logger = get_logger(__name__)
tracer = get_tracer(__name__)


class MediaDocument(BaseModel):
    """Document model for media files."""

    id: str = Field(description="Unique document identifier")
    title: str = Field(description="Filename-derived title")
    source_path: str = Field(description="Original file path")
    source: str = Field(description="Source directory (metube/tubesync)")
    document_type: str = Field(default="media_file")
    domain: str = Field(default="media_ingest")
    metadata: dict[str, Any] = Field(default_factory=dict)


def _file_to_document(file_info: dict[str, Any]) -> MediaDocument:
    """Convert enriched file info to a MediaDocument."""
    filename = file_info["filename"]
    title = os.path.splitext(filename)[0]
    source = "metube" if "metube" in file_info["source_dir"] else "tubesync"

    return MediaDocument(
        id=f"media-{source}-{title}",
        title=title,
        source_path=file_info["path"],
        source=source,
        metadata={
            "extension": file_info["extension"],
            "size_bytes": file_info["size_bytes"],
            **file_info.get("metadata", {}),
        },
    )


@asset(
    group_name="media_ingest",
    description="Transform media metadata into Document objects",
    compute_kind="transform",
    metadata={"layer": "silver"},
)
def media_documents(
    context: AssetExecutionContext,
    media_metadata: list[dict[str, Any]],
) -> Output[list[MediaDocument]]:
    with trace_operation("media_documents", tracer, {"code_location": "media_ingest", "layer": "silver", "record_count": len(media_metadata)}):
        logger.info("Starting media_documents transformation for %d files", len(media_metadata))
        documents = [_file_to_document(f) for f in media_metadata]

        by_source: dict[str, int] = {}
        for doc in documents:
            by_source[doc.source] = by_source.get(doc.source, 0) + 1

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_documents", layer="silver").inc(len(documents))
        logger.info("media_documents complete: %d documents", len(documents))
        context.log.info(f"Produced {len(documents)} documents")

        return Output(
            documents,
            metadata={
                "total_documents": len(documents),
                "by_source": MetadataValue.json(by_source),
            },
        )
