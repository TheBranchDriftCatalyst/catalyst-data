"""Silver: Transform raw entities into unified Document objects."""

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from open_leaks.core.document import Document
from open_leaks.entities import Cable, CourtDocument, OffshoreEntity

logger = get_logger(__name__)


def _cable_to_document(cable: Cable) -> Document:
    content_parts = [cable.subject]
    if cable.origin:
        content_parts.append(f"Origin: {cable.origin}")
    if cable.content:
        content_parts.append(cable.content)

    return Document(
        id=f"wikileaks-cable-{cable.id}",
        title=cable.subject or f"Cable {cable.id}",
        content="\n\n".join(content_parts),
        source="wikileaks",
        source_url=cable.source_url,
        document_type="cable",
        domain="open_leaks",
        entity_type="Cable",
        metadata={
            "date": cable.date,
            "origin": cable.origin,
            "classification": cable.classification,
            "tags": cable.tags,
        },
    )


def _offshore_entity_to_document(entity: OffshoreEntity) -> Document:
    content_parts = [entity.name]
    if entity.jurisdiction:
        content_parts.append(f"Jurisdiction: {entity.jurisdiction}")
    if entity.country:
        content_parts.append(f"Country: {entity.country}")
    if entity.status:
        content_parts.append(f"Status: {entity.status}")

    return Document(
        id=f"icij-{entity.source_dataset}-{entity.id}",
        title=entity.name or f"Entity {entity.id}",
        content=". ".join(content_parts),
        source=f"icij-{entity.source_dataset}",
        source_url=entity.source_url,
        document_type="offshore_entity",
        domain="open_leaks",
        entity_type="OffshoreEntity",
        metadata={
            "entity_type": entity.entity_type,
            "jurisdiction": entity.jurisdiction,
            "country": entity.country,
            "source_dataset": entity.source_dataset,
            "incorporation_date": entity.incorporation_date,
        },
    )


def _court_doc_to_document(doc: CourtDocument) -> Document:
    content_parts = [doc.title]
    if doc.case_number:
        content_parts.append(f"Case: {doc.case_number}")
    if doc.content:
        content_parts.append(doc.content)

    return Document(
        id=f"epstein-{doc.id}",
        title=doc.title or f"Document {doc.id}",
        content="\n\n".join(content_parts),
        source="epstein-court-files",
        source_url=doc.source_url,
        document_type="court_document",
        domain="open_leaks",
        entity_type="CourtDocument",
        metadata={
            "case_number": doc.case_number,
            "document_type": doc.document_type,
            "date_filed": doc.date_filed,
            "page_count": doc.page_count,
        },
    )


@asset(
    group_name="leaks",
    description="Transform raw leak entities into unified Document objects",
    compute_kind="transform",
    metadata={"layer": "silver"},
)
def leak_documents(
    context: AssetExecutionContext,
    wikileaks_cables: list[Cable],
    icij_offshore_entities: list[OffshoreEntity],
    epstein_court_docs: list[CourtDocument],
) -> Output[list[Document]]:
    logger.info("Starting leak_documents transformation")
    documents: list[Document] = []

    for cable in wikileaks_cables:
        documents.append(_cable_to_document(cable))

    for entity in icij_offshore_entities:
        documents.append(_offshore_entity_to_document(entity))

    for doc in epstein_court_docs:
        documents.append(_court_doc_to_document(doc))

    ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="leak_documents", layer="silver").inc(len(documents))
    logger.info("leak_documents transformation complete count=%d", len(documents))
    context.log.info(
        f"Produced {len(documents)} documents "
        f"(cables={len(wikileaks_cables)}, icij_entities={len(icij_offshore_entities)}, "
        f"court_docs={len(epstein_court_docs)})"
    )

    return Output(
        documents,
        metadata={
            "total_documents": len(documents),
            "by_source": MetadataValue.json({
                "wikileaks_cables": len(wikileaks_cables),
                "icij_offshore_entities": len(icij_offshore_entities),
                "epstein_court_docs": len(epstein_court_docs),
            }),
        },
    )
