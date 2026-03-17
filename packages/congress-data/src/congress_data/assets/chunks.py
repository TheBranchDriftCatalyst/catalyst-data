"""Silver: Text chunking for downstream LLM and embedding stages.

Routes by document_type for optimal chunk sizes:
- bill: 400/100 (short summaries, most fit in 1-2 chunks)
- member_profile, committee_profile: passthrough (too short to chunk)
"""

from dagster import AssetExecutionContext, Output, asset
from dagster_io import ChunkingResource, TextChunk

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from congress_data.core.document import Document

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Optimal chunk sizes per document type
CHUNK_PROFILES = {
    "bill": {"chunk_size": 400, "chunk_overlap": 100},
}
PASSTHROUGH_TYPES = {"member_profile", "committee_profile"}


@asset(
    group_name="congress",
    description="Chunk Congress documents for embedding and LLM extraction",
    compute_kind="python",
    metadata={"layer": "silver"},
)
def congress_chunks(
    context: AssetExecutionContext,
    chunking: ChunkingResource,
    congress_documents: list[Document],
) -> Output[list[TextChunk]]:
    with trace_operation("congress_chunks", tracer, {"code_location": "congress_data", "layer": "silver", "document_count": len(congress_documents)}):
        logger.info("Starting congress_chunks chunking for %d documents", len(congress_documents))
        all_chunks: list[TextChunk] = []
        stats: dict[str, int] = {}

        for doc in congress_documents:
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

        ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_chunks", layer="silver").inc(len(all_chunks))
        logger.info("congress_chunks complete: %d documents -> %d chunks", len(congress_documents), len(all_chunks))
        context.log.info(
            f"Chunked {len(congress_documents)} documents into {len(all_chunks)} chunks: "
            + ", ".join(f"{k}={v}" for k, v in stats.items())
        )
        return Output(
            all_chunks,
            metadata={
                "document_count": len(congress_documents),
                "chunk_count": len(all_chunks),
                "chunks_by_type": stats,
            },
        )
