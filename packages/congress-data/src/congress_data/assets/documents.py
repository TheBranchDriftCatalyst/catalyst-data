"""Stage 2: Transform raw entities into unified Document objects."""

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from congress_data.core.document import Document
from congress_data.entities import Bill, Committee, Member

logger = get_logger(__name__)
tracer = get_tracer(__name__)


def _bill_to_document(bill: Bill) -> Document:
    content_parts = [f"Bill {bill.number} ({bill.chamber})"]
    if bill.summary:
        content_parts.append(bill.summary)
    if bill.title:
        content_parts.append(bill.title)

    return Document(
        id=f"congress-bill-{bill.id}",
        title=bill.title,
        content=". ".join(content_parts),
        source="congress.gov",
        source_url=bill.source_url,
        document_type="bill",
        domain="congress",
        entity_type="Bill",
        metadata={
            "congress": bill.congress,
            "bill_type": bill.bill_type,
            "chamber": bill.chamber,
            "policy_area": bill.policy_area,
            "introduced_date": str(bill.introduced_date) if bill.introduced_date else None,
        },
        sections={
            "latest_action": bill.latest_action_text or "",
        },
    )


def _member_to_document(member: Member) -> Document:
    content_parts = [member.name]
    if member.party and member.state:
        content_parts.append(f"{member.party}-{member.state}")
    if member.chamber:
        content_parts.append(f"Chamber: {member.chamber}")

    return Document(
        id=f"congress-member-{member.id}",
        title=member.name,
        content=". ".join(content_parts),
        source="congress.gov",
        source_url=member.source_url,
        document_type="member_profile",
        domain="congress",
        entity_type="Member",
        metadata={
            "bioguide_id": member.bioguide_id,
            "party": member.party,
            "state": member.state,
            "district": member.district,
            "terms_served": member.terms_served,
        },
    )


def _committee_to_document(committee: Committee) -> Document:
    content_parts = [committee.name]
    if committee.chamber:
        content_parts.append(f"Chamber: {committee.chamber}")
    if committee.committee_type:
        content_parts.append(f"Type: {committee.committee_type}")

    return Document(
        id=f"congress-committee-{committee.id}",
        title=committee.name,
        content=". ".join(content_parts),
        source="congress.gov",
        source_url=committee.source_url,
        document_type="committee_profile",
        domain="congress",
        entity_type="Committee",
        metadata={
            "system_code": committee.system_code,
            "chamber": committee.chamber,
            "committee_type": committee.committee_type,
            "parent_committee": committee.parent_committee,
        },
    )


@asset(
    group_name="congress",
    description="Transform raw Congress entities into unified Document objects",
    compute_kind="transform",
    metadata={"layer": "silver"},
)
def congress_documents(
    context: AssetExecutionContext,
    congress_bills: list[Bill],
    congress_members: list[Member],
    congress_committees: list[Committee],
) -> Output[list[Document]]:
    with trace_operation("congress_documents", tracer, {"code_location": "congress_data", "layer": "silver", "bill_count": len(congress_bills), "member_count": len(congress_members), "committee_count": len(congress_committees)}):
        logger.info("Starting congress_documents transformation")
        documents: list[Document] = []

        for bill in congress_bills:
            documents.append(_bill_to_document(bill))

        for member in congress_members:
            documents.append(_member_to_document(member))

        for committee in congress_committees:
            documents.append(_committee_to_document(committee))

        ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_documents", layer="silver").inc(len(documents))
        logger.info("congress_documents transformation complete count=%d", len(documents))
        context.log.info(
            f"Produced {len(documents)} documents "
            f"(bills={len(congress_bills)}, members={len(congress_members)}, "
            f"committees={len(congress_committees)})"
        )

        return Output(
            documents,
            metadata={
                "total_documents": len(documents),
                "by_type": MetadataValue.json({
                    "bills": len(congress_bills),
                    "members": len(congress_members),
                    "committees": len(congress_committees),
                }),
            },
        )
