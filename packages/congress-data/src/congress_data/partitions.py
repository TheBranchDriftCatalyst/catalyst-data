"""Dynamic partition definitions for per-entity congress processing.

Two partition sets:
- congress_bill: keyed by "{congress}-{bill_type}-{number}" (e.g. "119-hr-1234")
- congress_member: keyed by bioguide_id (e.g. "S000148")
"""

from dagster import DynamicPartitionsDefinition

bill_partitions = DynamicPartitionsDefinition(name="congress_bill")
member_partitions = DynamicPartitionsDefinition(name="congress_member")


def make_bill_partition_key(congress: int, bill_type: str, number: int | str) -> str:
    """Build a partition key for a bill: {congress}-{bill_type}-{number}."""
    return f"{congress}-{bill_type.lower()}-{number}"


def parse_bill_partition_key(key: str) -> tuple[int, str, int]:
    """Parse a bill partition key into (congress, bill_type, number)."""
    parts = key.split("-", 2)
    return int(parts[0]), parts[1], int(parts[2])
