"""Stage 1: Raw data extraction from Congress.gov API."""

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from congress_data.client import CongressAPIClient
from congress_data.config import CongressionalConfig
from congress_data.entities import Bill, Committee, Member


@asset(
    group_name="congress",
    description="Extract bills from Congress.gov API",
    compute_kind="extract",
    metadata={"layer": "bronze"},
)
def congress_bills(
    context: AssetExecutionContext, config: CongressionalConfig
) -> Output[list[Bill]]:
    with CongressAPIClient(api_key=config.congress_api_key) as client:
        bills: list[Bill] = []
        for bill_data in client.iterate_bills(
            congress=config.congress_number, max_bills=config.max_bills
        ):
            bill = Bill.from_api_response(bill_data, congress=config.congress_number)
            bills.append(bill)

        context.log.info(f"Extracted {len(bills)} bills from congress {config.congress_number}")

    return Output(
        bills,
        metadata={
            "congress": config.congress_number,
            "count": len(bills),
            "sample_titles": MetadataValue.json([b.title[:100] for b in bills[:5]]),
        },
    )


@asset(
    group_name="congress",
    description="Extract members from Congress.gov API",
    compute_kind="extract",
    metadata={"layer": "bronze"},
)
def congress_members(
    context: AssetExecutionContext, config: CongressionalConfig
) -> Output[list[Member]]:
    with CongressAPIClient(api_key=config.congress_api_key) as client:
        members: list[Member] = []
        for member_data in client.iterate_members(
            congress=config.congress_number, max_members=config.max_members
        ):
            member = Member.from_api_response(member_data)
            members.append(member)

        context.log.info(
            f"Extracted {len(members)} members from congress {config.congress_number}"
        )

    return Output(
        members,
        metadata={
            "congress": config.congress_number,
            "count": len(members),
            "sample_names": MetadataValue.json([m.name for m in members[:5]]),
        },
    )


@asset(
    group_name="congress",
    description="Extract committees from Congress.gov API",
    compute_kind="extract",
    metadata={"layer": "bronze"},
)
def congress_committees(
    context: AssetExecutionContext, config: CongressionalConfig
) -> Output[list[Committee]]:
    with CongressAPIClient(api_key=config.congress_api_key) as client:
        committees: list[Committee] = []
        for committee_data in client.iterate_committees(
            congress=config.congress_number, max_committees=config.max_committees
        ):
            committee = Committee.from_api_response(committee_data)
            committees.append(committee)

        context.log.info(
            f"Extracted {len(committees)} committees from congress {config.congress_number}"
        )

    return Output(
        committees,
        metadata={
            "congress": config.congress_number,
            "count": len(committees),
            "sample_names": MetadataValue.json([c.name for c in committees[:5]]),
        },
    )
