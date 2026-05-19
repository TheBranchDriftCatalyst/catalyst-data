"""Congress-data asset modules.

Head: head.py — unpartitioned discovery + manifests
Bill tail: bill_tail.py — partitioned per-bill pipeline (bronze/silver)
Member tail: member_tail.py — partitioned per-member pipeline (bronze/silver)
Gold: gold.py — factory-generated extraction assets for both pipelines
"""

from congress_data.assets.bill_tail import (
    bill_actions,
    bill_amendments,
    bill_chunks,
    bill_cosponsors,
    bill_detail,
    bill_document,
    bill_full_text,
    bill_text_versions,
)
from congress_data.assets.gold import bill_gold_assets, member_gold_assets
from congress_data.assets.head import (
    bills_list_incremental,
    bills_manifest,
    members_list_incremental,
    members_manifest,
)
from congress_data.assets.member_tail import (
    member_chunks,
    member_committee_assignments,
    member_cosponsored,
    member_detail,
    member_document,
    member_sponsored,
)
from congress_data.assets.structured_assertions import (
    congress_structured_assertions,
)

__all__ = [
    # Head
    "bills_list_incremental",
    "bills_manifest",
    "members_list_incremental",
    "members_manifest",
    # Bill tail (bronze/silver)
    "bill_detail",
    "bill_actions",
    "bill_cosponsors",
    "bill_text_versions",
    "bill_amendments",
    "bill_full_text",
    "bill_document",
    "bill_chunks",
    # Bill gold (factory-generated)
    "bill_gold_assets",
    # Member tail (bronze/silver)
    "member_detail",
    "member_committee_assignments",
    "member_sponsored",
    "member_cosponsored",
    "member_document",
    "member_chunks",
    # Member gold (factory-generated)
    "member_gold_assets",
    # Structured (non-AMR) assertions from API fields
    "congress_structured_assertions",
]
