"""Congress-data asset modules.

Head: head.py — unpartitioned discovery + manifests
Bill tail: bill_tail.py — partitioned per-bill pipeline
Member tail: member_tail.py — partitioned per-member pipeline
"""

from congress_data.assets.bill_tail import (
    bill_actions,
    bill_amendments,
    bill_assertions,
    bill_chunks,
    bill_cosponsors,
    bill_detail,
    bill_document,
    bill_embeddings,
    bill_mentions,
    bill_text_versions,
)
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
    member_embeddings,
    member_mentions,
    member_sponsored,
)

__all__ = [
    # Head
    "bills_list_incremental",
    "bills_manifest",
    "members_list_incremental",
    "members_manifest",
    # Bill tail
    "bill_detail",
    "bill_actions",
    "bill_cosponsors",
    "bill_text_versions",
    "bill_amendments",
    "bill_document",
    "bill_chunks",
    "bill_mentions",
    "bill_assertions",
    "bill_embeddings",
    # Member tail
    "member_detail",
    "member_committee_assignments",
    "member_sponsored",
    "member_cosponsored",
    "member_document",
    "member_chunks",
    "member_mentions",
    "member_embeddings",
]
