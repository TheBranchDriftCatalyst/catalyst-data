"""Stage 1: Raw data extraction from Congress.gov API."""

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from congress_data.client import CongressAPIClient
from congress_data.config import CongressionalConfig
from congress_data.entities import Bill, Committee, Member

logger = get_logger(__name__)
tracer = get_tracer(__name__)


@asset(
    group_name="congress",
    description="Extract bills from Congress.gov API",
    compute_kind="extract",
    metadata={"layer": "bronze"},
)
def congress_bills(
    context: AssetExecutionContext, config: CongressionalConfig
) -> Output[list[Bill]]:
    with trace_operation("congress_bills", tracer, {"code_location": "congress_data", "layer": "bronze", "congress_number": config.congress_number}):
        logger.info("Starting congress_bills extraction for congress=%d", config.congress_number)
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            bills: list[Bill] = []
            for bill_data in client.iterate_bills(
                congress=config.congress_number, max_bills=config.max_bills
            ):
                bill = Bill.from_api_response(bill_data, congress=config.congress_number)
                bills.append(bill)

            ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_bills", layer="bronze").inc(len(bills))
            logger.info("congress_bills extraction complete count=%d", len(bills))
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
    with trace_operation("congress_members", tracer, {"code_location": "congress_data", "layer": "bronze", "congress_number": config.congress_number}):
        logger.info("Starting congress_members extraction for congress=%d", config.congress_number)
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            members: list[Member] = []
            for member_data in client.iterate_members(
                congress=config.congress_number, max_members=config.max_members
            ):
                member = Member.from_api_response(member_data)
                members.append(member)

            ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_members", layer="bronze").inc(len(members))
            logger.info("congress_members extraction complete count=%d", len(members))
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
    with trace_operation("congress_committees", tracer, {"code_location": "congress_data", "layer": "bronze", "congress_number": config.congress_number}):
        logger.info("Starting congress_committees extraction for congress=%d", config.congress_number)
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            committees: list[Committee] = []
            for committee_data in client.iterate_committees(
                congress=config.congress_number, max_committees=config.max_committees
            ):
                committee = Committee.from_api_response(committee_data)
                committees.append(committee)

            ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="congress_committees", layer="bronze").inc(len(committees))
            logger.info("congress_committees extraction complete count=%d", len(committees))
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
