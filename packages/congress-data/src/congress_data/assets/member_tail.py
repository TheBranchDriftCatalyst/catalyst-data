"""TAIL assets: partitioned per-member pipeline (bronze → silver → gold).

Each asset is partitioned on congress_member (key = bioguide_id).
Triggered by congress_member_sensor.

Bronze: member_detail, member_committee_assignments, member_sponsored, member_cosponsored
Silver: member_document, member_chunks
Gold: member_mentions, member_embeddings
"""

from dagster import (
    AssetExecutionContext,
    AssetIn,
    Output,
    RetryPolicy,
    asset,
)
from langchain_core.messages import HumanMessage, SystemMessage

from congress_data.client import CongressAPIClient
from congress_data.config import CongressionalConfig
from congress_data.core.document import Document
from congress_data.entities import Member, Term
from congress_data.partitions import member_partitions
from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    ChunkingResource,
    EmbeddingResource,
    LLMResource,
    MentionExtractionResult,
    TextChunk,
    build_mentions,
)
from dagster_io.logging import get_logger
from dagster_io.observability import get_tracer, trace_operation

logger = get_logger(__name__)
tracer = get_tracer(__name__)

CONGRESS_API_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "env_from": [
                {"secret_ref": {"name": "congress-data-secrets"}},
            ],
            "resources": {
                "requests": {"cpu": "100m", "memory": "256Mi"},
                "limits": {"cpu": "300m", "memory": "512Mi"},
            },
        },
    },
}

CONGRESS_API_RETRY = RetryPolicy(max_retries=2, delay=30)
CONCURRENCY_TAG = {"dagster/concurrency_key": "congress_api"}


# ══════════════════════════════════════════════════════════════════════════════
# BRONZE
# ══════════════════════════════════════════════════════════════════════════════


@asset(
    group_name="congress",
    description="Fetch member detail from Congress.gov API (includes terms, depiction)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    partitions_def=member_partitions,
    op_tags={**CONGRESS_API_K8S_CONFIG, **CONCURRENCY_TAG},
    retry_policy=CONGRESS_API_RETRY,
)
def member_detail(
    context: AssetExecutionContext,
    config: CongressionalConfig,
) -> Output[dict]:
    """Returns raw member detail + parsed Terms for downstream use."""
    bioguide_id = context.partition_key

    with trace_operation("member_detail", tracer, {"partition": bioguide_id, "layer": "bronze"}):
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            data = client.get_member_detail(bioguide_id)

        member = Member.from_api_detail(data)
        terms = Term.from_member_detail(data)

        result = {
            "member": member.model_dump(),
            "terms": [t.model_dump() for t in terms],
            "raw": data,
        }

        context.log.info(f"Member detail: {member.name} ({bioguide_id}), {len(terms)} terms")
        return Output(
            result,
            metadata={
                "bioguide_id": bioguide_id,
                "name": member.name,
                "terms_count": len(terms),
            },
        )


@asset(
    group_name="congress",
    description="Extract committee assignments from member detail terms",
    compute_kind="transform",
    metadata={"layer": "bronze"},
    partitions_def=member_partitions,
)
def member_committee_assignments(
    context: AssetExecutionContext,
    member_detail: dict,
) -> Output[list[dict]]:
    """Derive committee assignments from member detail data."""
    bioguide_id = context.partition_key
    raw = member_detail.get("raw", {})
    member_data = raw.get("member", raw)

    # Extract committee memberships if available
    committees = []
    terms = member_data.get("terms", [])
    if isinstance(terms, dict):
        terms = terms.get("item", [])

    # Committee data may be nested in terms or at top level
    for term in terms:
        for comm in term.get("committees", []):
            committees.append(
                {
                    "bioguide_id": bioguide_id,
                    "system_code": comm.get("systemCode", ""),
                    "name": comm.get("name", ""),
                    "congress": term.get("congress"),
                    "chamber": term.get("chamber"),
                }
            )

    context.log.info(f"Member committees: {bioguide_id} — {len(committees)} assignments")
    return Output(committees, metadata={"count": len(committees)})


@asset(
    group_name="congress",
    description="Fetch member sponsored legislation (append-only)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    partitions_def=member_partitions,
    io_manager_key="append_io_manager",
    op_tags={**CONGRESS_API_K8S_CONFIG, **CONCURRENCY_TAG},
    retry_policy=CONGRESS_API_RETRY,
)
def member_sponsored(
    context: AssetExecutionContext,
    config: CongressionalConfig,
) -> Output[list[dict]]:
    bioguide_id = context.partition_key

    with trace_operation("member_sponsored", tracer, {"partition": bioguide_id, "layer": "bronze"}):
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            bills = [
                {
                    "bioguide_id": bioguide_id,
                    "bill_type": b.get("type", "").lower(),
                    "bill_number": b.get("number"),
                    "congress": b.get("congress"),
                    "title": b.get("latestTitle", b.get("title", "")),
                    "introduced_date": b.get("introducedDate"),
                    "policy_area": b.get("policyArea", {}).get("name") if b.get("policyArea") else None,
                }
                for b in client.get_member_sponsored(bioguide_id)
            ]

        context.log.info(f"Member sponsored: {bioguide_id} — {len(bills)} bills")
        return Output(bills, metadata={"count": len(bills)})


@asset(
    group_name="congress",
    description="Fetch member cosponsored legislation (append-only)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    partitions_def=member_partitions,
    io_manager_key="append_io_manager",
    op_tags={**CONGRESS_API_K8S_CONFIG, **CONCURRENCY_TAG},
    retry_policy=CONGRESS_API_RETRY,
)
def member_cosponsored(
    context: AssetExecutionContext,
    config: CongressionalConfig,
) -> Output[list[dict]]:
    bioguide_id = context.partition_key

    with trace_operation("member_cosponsored", tracer, {"partition": bioguide_id, "layer": "bronze"}):
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            bills = [
                {
                    "bioguide_id": bioguide_id,
                    "bill_type": b.get("type", "").lower(),
                    "bill_number": b.get("number"),
                    "congress": b.get("congress"),
                    "title": b.get("latestTitle", b.get("title", "")),
                    "introduced_date": b.get("introducedDate"),
                }
                for b in client.get_member_cosponsored(bioguide_id)
            ]

        context.log.info(f"Member cosponsored: {bioguide_id} — {len(bills)} bills")
        return Output(bills, metadata={"count": len(bills)})


# ══════════════════════════════════════════════════════════════════════════════
# SILVER
# ══════════════════════════════════════════════════════════════════════════════


@asset(
    group_name="congress",
    description="Transform member detail into rich Document",
    compute_kind="transform",
    metadata={"layer": "silver"},
    partitions_def=member_partitions,
    ins={
        "member_committee_assignments": AssetIn(input_manager_key="optional_io_manager"),
    },
)
def member_document(
    context: AssetExecutionContext,
    member_detail: dict,
    member_committee_assignments: list[dict] | None,
) -> Output[Document]:
    bioguide_id = context.partition_key
    # Handle both direct dict and nested {"member": ..., "terms": ...} formats
    if "member" in member_detail:
        member_data = member_detail["member"]
        terms_data = member_detail.get("terms", [])
    else:
        member_data = member_detail
        terms_data = []

    name = member_data.get("name", bioguide_id)

    # Build content
    parts = [name]
    if terms_data:
        latest = terms_data[-1] if terms_data else {}
        party = latest.get("party", "")
        state = latest.get("state", "")
        chamber = latest.get("chamber", "")
        if party and state:
            parts.append(f"{party}-{state}")
        if chamber:
            parts.append(f"Chamber: {chamber}")
        parts.append(f"Terms served: {len(terms_data)}")

    if member_committee_assignments:
        committee_names = list({c.get("name", "") for c in member_committee_assignments if c.get("name")})[:5]
        if committee_names:
            parts.append(f"Committees: {', '.join(committee_names)}")

    doc = Document(
        id=f"congress-member-{bioguide_id}",
        title=name,
        content=". ".join(parts),
        source="congress.gov",
        source_url=member_data.get("api_url"),
        document_type="member_profile",
        domain="congress",
        entity_type="Member",
        metadata={
            "bioguide_id": bioguide_id,
            "terms_served": len(terms_data),
            "committee_count": len(member_committee_assignments or []),
        },
    )

    context.log.info(f"Member document: {doc.id}")
    return Output(doc, metadata={"doc_id": doc.id})


@asset(
    group_name="congress",
    description="Chunk member document (passthrough — profiles are short)",
    compute_kind="python",
    metadata={"layer": "silver"},
    partitions_def=member_partitions,
)
def member_chunks(
    context: AssetExecutionContext,
    chunking: ChunkingResource,
    member_document: Document,
) -> Output[list[TextChunk]]:
    meta = {"source": member_document.source, "document_type": "member_profile", "domain": "congress"}
    chunks = chunking.passthrough(member_document.id, member_document.title, member_document.content, metadata=meta)

    context.log.info(f"Member chunks: {member_document.id} → {len(chunks)} chunks")
    return Output(chunks, metadata={"chunk_count": len(chunks)})


# ══════════════════════════════════════════════════════════════════════════════
# GOLD
# ══════════════════════════════════════════════════════════════════════════════


@asset(
    group_name="congress",
    description="Extract entity mentions from member profile via LLM",
    compute_kind="llm",
    metadata={"layer": "gold"},
    partitions_def=member_partitions,
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def member_mentions(
    context: AssetExecutionContext,
    llm: LLMResource,
    member_chunks: list[TextChunk],
) -> Output[list]:
    with trace_operation("member_mentions", tracer, {"partition": context.partition_key, "layer": "gold"}):
        chain = llm.with_structured_output(MentionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(content="Extract all named entity mentions from this congressional member profile."),
                HumanMessage(content=f"Extract mentions:\n\n{chunk.text}"),
            ],
            member_chunks,
            operation="mention_extract",
        )

        all_mentions = build_mentions(member_chunks, results, llm_model=llm.model, code_location="congress_data")

        context.log.info(f"Member mentions: {len(all_mentions)}")
        return Output(all_mentions, metadata={"mention_count": len(all_mentions)})


@asset(
    group_name="congress",
    description="Generate embeddings for member profile chunks",
    compute_kind="embedding",
    metadata={"layer": "gold"},
    partitions_def=member_partitions,
)
def member_embeddings(
    context: AssetExecutionContext,
    embeddings: EmbeddingResource,
    member_chunks: list[TextChunk],
) -> Output[list[dict]]:
    with trace_operation("member_embeddings", tracer, {"partition": context.partition_key, "layer": "gold"}):
        texts = [chunk.text for chunk in member_chunks]
        vectors = embeddings.embed(texts) if texts else []

        embedded = [
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "text": chunk.text,
                "embedding": vec,
                "metadata": chunk.metadata,
            }
            for chunk, vec in zip(member_chunks, vectors, strict=True)
        ]

        context.log.info(f"Member embeddings: {len(embedded)} vectors")
        return Output(embedded, metadata={"vector_count": len(embedded)})
