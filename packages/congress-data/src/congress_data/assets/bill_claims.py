"""Gold-layer bill_claims asset — LLM-synthesised legal claims.

Composes the AMR primitives (bill_assertions) + the bill text
(bill_document) into 5–15 structured legal claims per bill using the
``claim_synthesis`` prompt + the closed predicate vocab from the
congress label pack.

Each claim is a ``BillClaim`` (LegalRuleML-style normative statement:
actor + operator + action + typed conditions + exceptions + penalty +
temporal_window + verbatim source sentence + LLM self-flag). Wire
shape is deliberately separate from the flat-SPO
``contracts_core.Assertion`` because legal claims benefit from typed
structure that wouldn't fit in a free-form ``qualifiers: dict``.

Output: ``gold/congress_data/bill/bill_claims/{partition}/data.jsonl``
— one row per claim, JSONL-encoded ``BillClaim``.

Design references in
``k8s/base/congress-data/prompts/claim_synthesis.prompt`` frontmatter.
"""

# NOTE: do NOT add `from __future__ import annotations` here — Dagster's
# op-definition validator does an `is` check on the `context` param's
# annotation against the canonical AssetExecutionContext class. With
# postponed evaluation enabled, the annotation is a *string* and the
# check fails with a confusing "must be annotated with
# AssetExecutionContext" error even though the source literally is.

import hashlib
import json
from datetime import UTC, datetime

from dagster import AssetExecutionContext, AssetIn, Output, asset

from catalyst_contracts_core import ExtractionMethod, Provenance
from catalyst_contracts_core.types import Assertion as ContractAssertion
from congress_data.claim_models import BillClaim, BillClaimsResult, operator_class
from congress_data.core.document import Document
from congress_data.partitions import bill_partitions, parse_bill_partition_key
from dagster_io import TextChunk
from dagster_io.llm import LLMResource
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.prompts import load_prompt, resolve_prompt_dir

logger = get_logger(__name__)
tracer = get_tracer(__name__)

_EXTRACTION_MODEL = "bill_claims_v1"

# AMR primitives passed to the LLM are noisy — we cap how many we
# include in the prompt so it doesn't blow the context window on a
# large bill. The top-N by confidence (or first N) is enough grounding.
_AMR_PRIMITIVES_CAP = 80


def _stable_claim_id(actor: str, operator: str, action: str, chunk_id: str) -> str:
    """Stable hash of (actor, operator, action, chunk_id). 16 hex chars."""
    payload = f"{actor}|{operator}|{action}|{chunk_id}".encode()
    return hashlib.md5(payload).hexdigest()[:16]


def _find_source_chunk(sentence_text: str, chunks: list[TextChunk]) -> str | None:
    """Substring-match the LLM-emitted sentence_text against the chunks
    list. Returns the chunk_id of the first containing chunk, or None
    when no chunk holds the sentence (the LLM may have lightly edited
    quoting — we don't fail in that case, we just leave the field
    null)."""
    if not sentence_text:
        return None
    needle = sentence_text.strip()
    if len(needle) < 10:
        return None
    for chunk in chunks:
        if needle in chunk.text:
            return chunk.chunk_id
    # Fallback — try a shorter prefix in case the LLM truncated trailing punctuation
    prefix = needle[:80]
    for chunk in chunks:
        if prefix in chunk.text:
            return chunk.chunk_id
    return None


def _build_amr_primitives_block(assertions: list[ContractAssertion]) -> str:
    """Render the AMR primitives as a compact JSONL block for the prompt.

    The LLM only needs a quick-scan grounding view — actor → predicate →
    object + confidence + frame. Drop the rest of the wire shape to
    save context tokens.
    """
    rows: list[str] = []
    for a in assertions[:_AMR_PRIMITIVES_CAP]:
        rows.append(
            json.dumps(
                {
                    "subject": a.subject_text,
                    "predicate": a.predicate,
                    "object": a.object_text or "",
                    "amr_frame": a.amr_frame,
                    "polarity": a.polarity,
                    "confidence": round(a.confidence, 2),
                }
            )
        )
    if not rows:
        return "(no AMR primitives extracted)"
    return "\n".join(rows)


def _build_user_message(
    bill: Document,
    partition: str,
    amr_primitives: list[ContractAssertion],
    chunks: list[TextChunk],
) -> str:
    """User message passed to the LLM. Two grounding inputs in clearly
    labelled sections so the prompt can reference them."""
    meta = bill.metadata or {}
    metadata_lines = "\n".join(
        f"  {k}: {v}"
        for k, v in {
            "partition": partition,
            "title": bill.title,
            "congress": meta.get("congress"),
            "bill_type": meta.get("bill_type"),
            "origin_chamber": meta.get("origin_chamber"),
            "policy_area": meta.get("policy_area"),
            "introduced_date": meta.get("introduced_date"),
            "became_law": meta.get("became_law"),
        }.items()
        if v not in (None, "")
    )
    return (
        f"## BILL\n\n"
        f"{metadata_lines}\n\n"
        f"### Full text\n\n"
        f"{bill.content or '(no full text materialised)'}\n\n"
        f"## AMR_PRIMITIVES\n\n"
        f"(Top {min(len(amr_primitives), _AMR_PRIMITIVES_CAP)} of {len(amr_primitives)} "
        f"primitives — JSONL, one per line. Use as grounding; do not copy verbatim.)\n\n"
        f"{_build_amr_primitives_block(amr_primitives)}\n"
    )


def _stamp_claim(
    claim: BillClaim,
    bill: Document,
    chunks: list[TextChunk],
    code_location: str,
) -> BillClaim:
    """Post-LLM-response: fill in claim_id, source_chunk_id, provenance."""
    chunk_id = _find_source_chunk(claim.sentence_text, chunks)
    claim.source_chunk_id = chunk_id
    claim.claim_id = _stable_claim_id(claim.actor, claim.operator.value, claim.action, chunk_id or "")
    claim.provenance = Provenance(
        source_document_id=bill.id or "",
        chunk_id=chunk_id or "",
        span_start=None,
        span_end=None,
        temporal_start_ms=None,
        temporal_end_ms=None,
        speaker_label=None,
        source_media_uri=None,
        extraction_method=ExtractionMethod.LLM,
        extraction_model=_EXTRACTION_MODEL,
        confidence=claim.confidence,
        timestamp=datetime.now(UTC).isoformat(),
        code_location=code_location,
    )
    return claim


@asset(
    name="bill_claims",
    group_name="bill",
    description=(
        "LLM-synthesised legal claims per bill. Composes the AMR "
        "primitives (bill_assertions) + bill text (bill_document) into "
        "5–15 LegalRuleML-style normative statements with closed-vocab "
        "operators, typed conditions, exceptions, and verbatim source "
        "sentences."
    ),
    compute_kind="llm",
    metadata={"layer": "gold"},
    partitions_def=bill_partitions,
    required_resource_keys={"llm"},
    ins={
        "bill_document": AssetIn(),
        "bill_assertions": AssetIn(),
        "bill_chunks": AssetIn(),
    },
)
def bill_claims(
    context: AssetExecutionContext,
    bill_document: Document,
    bill_assertions: list[ContractAssertion],
    bill_chunks: list[TextChunk],
) -> Output[list[BillClaim]]:
    llm: LLMResource = context.resources.llm
    partition = context.partition_key
    _congress, _bill_type, _number = parse_bill_partition_key(partition)

    with trace_operation(
        "bill_claims",
        tracer,
        {"partition": partition, "layer": "gold", "code_location": "congress_data"},
    ):
        context.log.info(
            "bill_claims: synthesising claims for %s — %d AMR primitives, %d chunks",
            partition,
            len(bill_assertions),
            len(bill_chunks),
        )

        # Resolve the synthesis prompt from the congress prompts dir,
        # falling back to PROMPT_REGISTRY_DIR env when the domain-scoped
        # path doesn't resolve (e.g. when the asset runs from a built
        # container where source layout differs).
        prompt_dir = resolve_prompt_dir(domain="congress-data")
        system_prompt = load_prompt(
            "claim_synthesis",
            fallback="You extract legal claims. Output BillClaimsResult JSON.",
            registry_dir=prompt_dir or None,
        )

        user_msg = _build_user_message(bill_document, partition, bill_assertions, bill_chunks)

        # Structured-output call — LangChain handles the JSON-schema
        # binding from our Pydantic BillClaimsResult so the LLM is
        # constrained to the closed predicate vocab + nested types.
        from langchain_core.messages import HumanMessage, SystemMessage

        # Anthropic/Claude via LiteLLM wraps tool-call output in a
        # {"parameter": "<stringified JSON>"} envelope that breaks
        # Pydantic validation. json_mode avoids the wrapper and
        # returns the raw object. OpenAI's gpt-4o-family handles
        # function-calling fine but json_mode also works for them.
        chain = llm.with_structured_output(BillClaimsResult, method="json_mode")
        result: BillClaimsResult = chain.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])

        # Stamp identifiers + provenance + source-chunk back-reference.
        stamped = [_stamp_claim(c, bill_document, bill_chunks, code_location="congress_data") for c in result.claims]

        # Count operator classes for asset metadata.
        deontic_count = sum(1 for c in stamped if operator_class(c.operator) == "deontic")
        structural_count = len(stamped) - deontic_count
        review_count = sum(1 for c in stamped if c.review_needed)

        ASSET_RECORDS_PROCESSED.labels(
            code_location="congress_data",
            asset_key="bill_claims",
            layer="gold",
        ).inc(len(stamped))

        context.log.info(
            "bill_claims: %d claims (%d deontic, %d structural, %d flagged for review)",
            len(stamped),
            deontic_count,
            structural_count,
            review_count,
        )

        return Output(
            stamped,
            metadata={
                "partition": partition,
                "bill_title": (bill_document.title or "")[:100],
                "claim_count": len(stamped),
                "deontic_count": deontic_count,
                "structural_count": structural_count,
                "review_needed_count": review_count,
                "model": llm.model,
                "extraction_model": _EXTRACTION_MODEL,
            },
        )
