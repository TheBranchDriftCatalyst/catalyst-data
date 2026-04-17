"""TAIL assets: partitioned per-bill pipeline (bronze → silver → gold).

Each asset is partitioned on congress_bill (key = {congress}-{bill_type}-{number}).
Triggered by congress_bill_sensor when manifest detects NEW or UPDATED bills.

Bronze: bill_detail, bill_actions, bill_cosponsors, bill_text_versions, bill_amendments
Silver: bill_document, bill_chunks
Gold: bill_mentions, bill_assertions, bill_embeddings
"""

from dagster import (
    AssetExecutionContext,
    Output,
    RetryPolicy,
    asset,
)
from langchain_core.messages import HumanMessage, SystemMessage

from congress_data.client import CongressAPIClient
from congress_data.config import CongressionalConfig
from congress_data.core.document import Document
from congress_data.entities import (
    Action,
    Amendment,
    BillDetail,
    BillVersion,
    Cosponsor,
)
from congress_data.partitions import bill_partitions, parse_bill_partition_key
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
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.prompts import load_prompt

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
# BRONZE — API fetch per partition
# ══════════════════════════════════════════════════════════════════════════════


@asset(
    group_name="congress",
    description="Fetch bill detail from Congress.gov API (includes inline sponsor)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    partitions_def=bill_partitions,
    op_tags={**CONGRESS_API_K8S_CONFIG, **CONCURRENCY_TAG},
    retry_policy=CONGRESS_API_RETRY,
)
def bill_detail(context: AssetExecutionContext, config: CongressionalConfig) -> Output[BillDetail]:
    pk = context.partition_key
    congress, bill_type, number = parse_bill_partition_key(pk)

    with trace_operation("bill_detail", tracer, {"partition": pk, "layer": "bronze"}):
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            data = client.get_bill_detail(congress, bill_type, number)
            detail = BillDetail.from_api_response(data)

        context.log.info(f"Bill detail: {detail.id} — {detail.title[:80]}")
        return Output(
            detail,
            metadata={
                "bill_id": detail.id,
                "title": detail.title[:100],
                "sponsor": detail.sponsor_name or "unknown",
                "cosponsor_count": detail.cosponsor_count,
                "action_count": detail.action_count,
                "became_law": detail.became_law,
            },
        )


@asset(
    group_name="congress",
    description="Fetch bill actions (append-only event log)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    partitions_def=bill_partitions,
    io_manager_key="append_io_manager",
    op_tags={**CONGRESS_API_K8S_CONFIG, **CONCURRENCY_TAG},
    retry_policy=CONGRESS_API_RETRY,
)
def bill_actions(context: AssetExecutionContext, config: CongressionalConfig) -> Output[list[Action]]:
    pk = context.partition_key
    congress, bill_type, number = parse_bill_partition_key(pk)
    bill_id = f"{bill_type}{number}-{congress}"

    with trace_operation("bill_actions", tracer, {"partition": pk, "layer": "bronze"}):
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            actions = [
                Action.from_api_response(a, bill_id=bill_id, sequence=i)
                for i, a in enumerate(client.get_bill_actions(congress, bill_type, number))
            ]

        context.log.info(f"Bill actions: {bill_id} — {len(actions)} actions")
        return Output(actions, metadata={"count": len(actions), "bill_id": bill_id})


@asset(
    group_name="congress",
    description="Fetch bill cosponsors (append-only with temporal data)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    partitions_def=bill_partitions,
    io_manager_key="append_io_manager",
    op_tags={**CONGRESS_API_K8S_CONFIG, **CONCURRENCY_TAG},
    retry_policy=CONGRESS_API_RETRY,
)
def bill_cosponsors(context: AssetExecutionContext, config: CongressionalConfig) -> Output[list[Cosponsor]]:
    pk = context.partition_key
    congress, bill_type, number = parse_bill_partition_key(pk)
    bill_id = f"{bill_type}{number}-{congress}"

    with trace_operation("bill_cosponsors", tracer, {"partition": pk, "layer": "bronze"}):
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            cosponsors = [
                Cosponsor.from_api_response(c, bill_id=bill_id)
                for c in client.get_bill_cosponsors(congress, bill_type, number)
            ]

        context.log.info(f"Bill cosponsors: {bill_id} — {len(cosponsors)}")
        return Output(cosponsors, metadata={"count": len(cosponsors), "bill_id": bill_id})


@asset(
    group_name="congress",
    description="Fetch bill text versions (immutable once published)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    partitions_def=bill_partitions,
    op_tags={**CONGRESS_API_K8S_CONFIG, **CONCURRENCY_TAG},
    retry_policy=CONGRESS_API_RETRY,
)
def bill_text_versions(context: AssetExecutionContext, config: CongressionalConfig) -> Output[list[BillVersion]]:
    pk = context.partition_key
    congress, bill_type, number = parse_bill_partition_key(pk)
    bill_id = f"{bill_type}{number}-{congress}"

    with trace_operation("bill_text_versions", tracer, {"partition": pk, "layer": "bronze"}):
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            versions = [
                BillVersion.from_api_response(v, bill_id=bill_id)
                for v in client.get_bill_text_versions(congress, bill_type, number)
            ]

        context.log.info(f"Bill text versions: {bill_id} — {len(versions)}")
        return Output(
            versions,
            metadata={
                "count": len(versions),
                "version_codes": [v.version_code for v in versions],
            },
        )


@asset(
    group_name="congress",
    description="Fetch bill amendments",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    partitions_def=bill_partitions,
    op_tags={**CONGRESS_API_K8S_CONFIG, **CONCURRENCY_TAG},
    retry_policy=CONGRESS_API_RETRY,
)
def bill_amendments(context: AssetExecutionContext, config: CongressionalConfig) -> Output[list[Amendment]]:
    pk = context.partition_key
    congress, bill_type, number = parse_bill_partition_key(pk)
    bill_id = f"{bill_type}{number}-{congress}"

    with trace_operation("bill_amendments", tracer, {"partition": pk, "layer": "bronze"}):
        with CongressAPIClient(api_key=config.congress_api_key) as client:
            amendments = [
                Amendment.from_api_response(a, bill_id=bill_id)
                for a in client.get_bill_amendments(congress, bill_type, number)
            ]

        context.log.info(f"Bill amendments: {bill_id} — {len(amendments)}")
        return Output(amendments, metadata={"count": len(amendments), "bill_id": bill_id})


# ══════════════════════════════════════════════════════════════════════════════
# BRONZE — Full text download (no API key, direct congress.gov CDN)
# ══════════════════════════════════════════════════════════════════════════════


def _strip_html(html: str) -> str:
    """Strip HTML tags and normalize whitespace to plain text."""
    import re

    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block elements with newlines
    text = re.sub(r"<(br|p|div|h\d|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    import html as html_mod

    text = html_mod.unescape(text)
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _download_text(url: str) -> str | None:
    """Download bill text from congress.gov with polite rate limiting."""
    import time

    import requests

    time.sleep(1.0)  # polite 1 req/sec to congress.gov CDN
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "catalyst-data/1.0 (research)"})
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning("Failed to download %s: %s", url, e)
        return None


@asset(
    group_name="congress",
    description="Download full bill text (HTM + plain text) for each version",
    compute_kind="download",
    metadata={"layer": "bronze"},
    partitions_def=bill_partitions,
    retry_policy=CONGRESS_API_RETRY,
)
def bill_full_text(
    context: AssetExecutionContext,
    bill_text_versions: list[BillVersion],
) -> Output[list[dict]]:
    """Download HTM from congress.gov for each text version.

    Stores both raw HTML (for display) and stripped plain text (for NER/chunking).
    No API key needed — these are public static files on the CDN.
    """
    results: list[dict] = []
    total_chars = 0

    for version in bill_text_versions:
        # Find the HTM format URL
        htm_url = None
        for fmt in version.formats:
            if fmt.get("type") == "Formatted Text" and fmt.get("url", "").endswith(".htm"):
                htm_url = fmt["url"]
                break

        if not htm_url:
            context.log.warning(f"No HTM URL for version {version.version_code}")
            continue

        context.log.info(f"Downloading {version.version_code}: {htm_url}")
        html_content = _download_text(htm_url)

        if html_content is None:
            continue

        plain_text = _strip_html(html_content)
        total_chars += len(plain_text)

        results.append(
            {
                "version_id": version.id,
                "bill_id": version.bill_id,
                "version_code": version.version_code,
                "version_name": version.version_name,
                "publish_date": str(version.publish_date) if version.publish_date else None,
                "source_url": htm_url,
                "html": html_content,
                "text": plain_text,
                "text_length": len(plain_text),
                "html_length": len(html_content),
            }
        )

    context.log.info(f"Bill full text: {len(results)} versions, {total_chars:,} chars total")
    return Output(
        results,
        metadata={
            "versions_downloaded": len(results),
            "versions_available": len(bill_text_versions),
            "total_text_chars": total_chars,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# SILVER — Document + Chunks
# ══════════════════════════════════════════════════════════════════════════════


@asset(
    group_name="congress",
    description="Transform bill detail + full text into rich Document for chunking",
    compute_kind="transform",
    metadata={"layer": "silver"},
    partitions_def=bill_partitions,
)
def bill_document(
    context: AssetExecutionContext,
    bill_detail: BillDetail,
    bill_full_text: list[dict],
) -> Output[Document]:
    # Build header from structured detail
    content_parts = [f"Bill {bill_detail.display_number} ({bill_detail.origin_chamber})"]
    content_parts.append(bill_detail.title)
    if bill_detail.short_title:
        content_parts.append(f"Short title: {bill_detail.short_title}")
    if bill_detail.sponsor_name:
        content_parts.append(
            f"Sponsor: {bill_detail.sponsor_name} ({bill_detail.sponsor_party}-{bill_detail.sponsor_state})"
        )
    if bill_detail.latest_action_text:
        content_parts.append(f"Latest action: {bill_detail.latest_action_text}")
    if bill_detail.policy_area:
        content_parts.append(f"Policy area: {bill_detail.policy_area}")
    if bill_detail.subjects:
        content_parts.append(f"Subjects: {', '.join(bill_detail.subjects[:10])}")

    header = ". ".join(content_parts)

    # Use the most recent text version as primary content
    # Priority: enrolled (enr) > engrossed (eh/eas) > reported (rh) > introduced (ih)
    version_priority = ["enr", "eas", "eh", "pcs", "rh", "rs", "ih", "is"]
    best_text = ""
    best_version = ""
    for code in version_priority:
        for ft in bill_full_text:
            if ft.get("version_code") == code and ft.get("text"):
                best_text = ft["text"]
                best_version = code
                break
        if best_text:
            break

    # Fallback: just use whatever we have
    if not best_text and bill_full_text:
        best_text = bill_full_text[0].get("text", "")
        best_version = bill_full_text[0].get("version_code", "unknown")

    # Combine header + full legislative text
    content = f"{header}\n\n--- FULL TEXT ({best_version.upper()}) ---\n\n{best_text}" if best_text else header

    doc = Document(
        id=f"congress-bill-{bill_detail.id}",
        title=bill_detail.title,
        content=content,
        source="congress.gov",
        source_url=bill_detail.api_url,
        document_type="bill",
        domain="congress",
        entity_type="Bill",
        metadata={
            "congress": bill_detail.congress,
            "bill_type": bill_detail.bill_type,
            "origin_chamber": bill_detail.origin_chamber,
            "policy_area": bill_detail.policy_area,
            "introduced_date": str(bill_detail.introduced_date) if bill_detail.introduced_date else None,
            "sponsor_bioguide": bill_detail.sponsor_bioguide_id,
            "cosponsor_count": bill_detail.cosponsor_count,
            "text_version_count": len(bill_full_text),
            "text_version_used": best_version or None,
            "became_law": bill_detail.became_law,
        },
        sections={
            "latest_action": bill_detail.latest_action_text or "",
        },
    )

    context.log.info(f"Bill document: {doc.id} ({len(content):,} chars, text version: {best_version or 'none'})")
    return Output(
        doc,
        metadata={
            "doc_id": doc.id,
            "content_length": len(doc.content),
            "text_version": best_version or "none",
            "has_full_text": bool(best_text),
        },
    )


@asset(
    group_name="congress",
    description="Chunk bill document for embedding and LLM extraction",
    compute_kind="python",
    metadata={"layer": "silver"},
    partitions_def=bill_partitions,
)
def bill_chunks(
    context: AssetExecutionContext,
    chunking: ChunkingResource,
    bill_document: Document,
) -> Output[list[TextChunk]]:
    meta = {
        "source": bill_document.source,
        "document_type": bill_document.document_type,
        "domain": bill_document.domain,
    }
    chunks = chunking.chunk_document(
        bill_document.id,
        bill_document.title,
        bill_document.content,
        metadata=meta,
        chunk_size=400,
        chunk_overlap=100,
    )

    context.log.info(f"Bill chunks: {bill_document.id} → {len(chunks)} chunks")
    return Output(chunks, metadata={"chunk_count": len(chunks)})


# ══════════════════════════════════════════════════════════════════════════════
# GOLD — LLM extraction + embeddings
# ══════════════════════════════════════════════════════════════════════════════


BILL_MENTION_PROMPT = load_prompt(
    "mentions/congress",
    fallback="""\
You are a named-entity extraction system specialized in U.S. Congressional data.
Given a text chunk, extract all named entity mentions with precise information.

Entity types to extract:
- PERSON: legislators, officials, witnesses, nominees
- ORG: committees, subcommittees, agencies, departments, lobbying groups
- GPE: countries, states, districts, cities
- LAW: bill numbers (H.R. XXX, S. XXX), public laws, acts, amendments
- EVENT: hearings, votes, elections, investigations
- MONEY: appropriations, budget figures, funding amounts
- NORP: political parties, caucuses, coalitions
- DATE: specific dates, date ranges, congressional sessions

For each entity, provide:
- text: the exact mention as it appears
- label: entity type from the list above
- context: the sentence fragment containing the entity
- span_start: character offset (0-based)
- span_end: character offset (exclusive)

Be exhaustive but avoid duplicates within the same span.""",
)


@asset(
    group_name="congress",
    description="Extract entity mentions from bill chunks via LLM",
    compute_kind="llm",
    metadata={"layer": "gold"},
    partitions_def=bill_partitions,
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def bill_mentions(
    context: AssetExecutionContext,
    llm: LLMResource,
    bill_chunks: list[TextChunk],
) -> Output[list]:

    with trace_operation("bill_mentions", tracer, {"partition": context.partition_key, "layer": "gold"}):
        chain = llm.with_structured_output(MentionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(content=BILL_MENTION_PROMPT),
                HumanMessage(content=f"Extract all entity mentions:\n\n{chunk.text}"),
            ],
            bill_chunks,
            operation="mention_extract",
        )

        all_mentions = build_mentions(bill_chunks, results, llm_model=llm.model, code_location="congress_data")

        ASSET_RECORDS_PROCESSED.labels(code_location="congress_data", asset_key="bill_mentions", layer="gold").inc(
            len(all_mentions)
        )
        context.log.info(f"Bill mentions: {len(all_mentions)} from {len(bill_chunks)} chunks")
        return Output(all_mentions, metadata={"mention_count": len(all_mentions)})


@asset(
    group_name="congress",
    description="Extract structured assertions from bill chunks via LLM",
    compute_kind="llm",
    metadata={"layer": "gold"},
    partitions_def=bill_partitions,
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def bill_assertions(
    context: AssetExecutionContext,
    llm: LLMResource,
    bill_chunks: list[TextChunk],
) -> Output[list]:
    from dagster_io import AssertionExtractionResult
    from dagster_io.asset_factories import build_assertions as _build

    with trace_operation("bill_assertions", tracer, {"partition": context.partition_key, "layer": "gold"}):
        chain = llm.with_structured_output(AssertionExtractionResult)
        results = llm.invoke_batch(
            chain,
            lambda chunk: [
                SystemMessage(
                    content=(
                        "Extract structured factual assertions from this Congressional text. "
                        "Focus on: who sponsored/cosponsored what, committee referrals, "
                        "voting actions, policy positions, funding amounts."
                    )
                ),
                HumanMessage(content=f"Extract assertions:\n\n{chunk.text}"),
            ],
            bill_chunks,
            operation="assertion_extract",
        )

        all_assertions = _build(bill_chunks, results, llm_model=llm.model, code_location="congress_data")

        context.log.info(f"Bill assertions: {len(all_assertions)} from {len(bill_chunks)} chunks")
        return Output(all_assertions, metadata={"assertion_count": len(all_assertions)})


@asset(
    group_name="congress",
    description="Generate embeddings for bill chunks",
    compute_kind="embedding",
    metadata={"layer": "gold"},
    partitions_def=bill_partitions,
)
def bill_embeddings(
    context: AssetExecutionContext,
    embeddings: EmbeddingResource,
    bill_chunks: list[TextChunk],
) -> Output[list[dict]]:
    with trace_operation("bill_embeddings", tracer, {"partition": context.partition_key, "layer": "gold"}):
        texts = [chunk.text for chunk in bill_chunks]
        vectors = embeddings.embed_documents(texts) if texts else []

        embedded = [
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "text": chunk.text,
                "embedding": vec,
                "metadata": chunk.metadata,
            }
            for chunk, vec in zip(bill_chunks, vectors, strict=True)
        ]

        context.log.info(f"Bill embeddings: {len(embedded)} vectors")
        return Output(embedded, metadata={"vector_count": len(embedded)})
