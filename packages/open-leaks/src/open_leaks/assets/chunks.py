"""Silver: Text chunking for downstream LLM and embedding stages.

Routes by document_type for optimal chunk sizes:
- cable: 1500/250 (semi-structured diplomatic text, respect paragraph boundaries)
- court_document: 2000/400 (dense legal text needs larger context windows)
- offshore_entity: passthrough (100-300 chars of metadata, chunking adds noise)
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import ChunkingResource, TextChunk

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from open_leaks.core.document import Document

logger = get_logger(__name__)
tracer = get_tracer(__name__)

CHUNK_PROFILES = {
    "cable": {"chunk_size": 1500, "chunk_overlap": 250},
    "court_document": {"chunk_size": 2000, "chunk_overlap": 400},
}
PASSTHROUGH_TYPES = {"offshore_entity"}


@asset(
    group_name="leaks",
    description="Chunk leak documents for embedding and LLM extraction",
    compute_kind="python",
    metadata={"layer": "silver"},
)
def leak_chunks(
    context: AssetExecutionContext,
    chunking: ChunkingResource,
    leak_documents: list[Document],
) -> Output[list[TextChunk]]:
    with trace_operation("leak_chunks", tracer, {"code_location": "open_leaks", "layer": "silver", "document_count": len(leak_documents)}):
        logger.info("Starting leak_chunks chunking for %d documents", len(leak_documents))
        all_chunks: list[TextChunk] = []
        stats: dict[str, int] = {}

        for doc in leak_documents:
            meta = {
                "source": doc.source,
                "document_type": doc.document_type,
                "domain": doc.domain,
            }

            if doc.document_type in PASSTHROUGH_TYPES:
                chunks = chunking.passthrough(doc.id, doc.title, doc.content, metadata=meta)
            else:
                profile = CHUNK_PROFILES.get(doc.document_type, {})
                chunks = chunking.chunk_document(
                    doc.id, doc.title, doc.content, metadata=meta, **profile
                )

            all_chunks.extend(chunks)
            stats[doc.document_type] = stats.get(doc.document_type, 0) + len(chunks)

        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="leak_chunks", layer="silver").inc(len(all_chunks))
        logger.info("leak_chunks complete: %d documents -> %d chunks", len(leak_documents), len(all_chunks))
        context.log.info(
            f"Chunked {len(leak_documents)} documents into {len(all_chunks)} chunks: "
            + ", ".join(f"{k}={v}" for k, v in stats.items())
        )
        return Output(
            all_chunks,
            metadata={
                "document_count": len(leak_documents),
                "chunk_count": len(all_chunks),
                "chunks_by_type": stats,
            },
        )
