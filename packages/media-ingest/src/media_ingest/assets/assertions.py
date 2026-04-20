"""Gold: Qualified assertion extraction from media transcription chunks via LLM.

Partitioned by document_id — each run extracts assertions from one document's chunks.
"""

import time

from dagster import AssetExecutionContext, Output, asset

import dagster_io.extraction as _extraction_mod
from dagster_io import (
    LLM_ASSET_K8S_CONFIG,
    Assertion,
    TextChunk,
)
from dagster_io.extraction import extract_validated
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from dagster_io.prompts import load_prompt
from dagster_io.versioning import code_version_from_modules
from media_ingest.partitions import media_partitions

_CODE_VERSION = code_version_from_modules(_extraction_mod)

logger = get_logger(__name__)
tracer = get_tracer(__name__)

ASSERTION_SYSTEM_PROMPT = load_prompt(
    "assertions",
    fallback="""\
You are a knowledge-graph extraction system specialized in transcribed audio/video content.
Given a text chunk from a media transcription (which may include speaker labels),
extract structured Subject-Predicate-Object assertions suitable for a knowledge graph.

## Output JSON Schema

Return a JSON object matching this schema:
{
  "assertions": [
    {
      "subject": "string -- the entity performing the action (NOT a pronoun)",
      "predicate": "string -- canonical relationship verb (see list below)",
      "object": "string -- the target entity or value (NOT a pronoun)",
      "confidence": "float 0.0-1.0",
      "negated": "boolean -- true if negated",
      "hedged": "boolean -- true if uncertain",
      "qualifiers": {
        "time": "string or empty",
        "location": "string or empty",
        "condition": "string or empty",
        "manner": "string or empty",
        "source_attribution": "string or empty"
      }
    }
  ]
}

## Canonical Predicates

Speech acts: states, claims, denies, confirms, acknowledges, questions,
  responds_to, references, discusses, criticizes, supports, opposes
Actions: owns, operates, leads, founded, works_for, member_of,
  provides, increased, decreased, departed, transfers, created

## Examples

### Example 1 -- Speaker claims
Input: "[SPEAKER_00] Russia has been supplying weapons to Iran since at least 2015, according to intelligence reports."
Output:
{"assertions": [
  {"subject": "SPEAKER_00", "predicate": "claims", "object": "Russia has been supplying weapons to Iran", "confidence": 0.9, "negated": false, "hedged": false, "qualifiers": {"time": "since at least 2015", "location": "", "condition": "", "manner": "", "source_attribution": "according to intelligence reports"}},
  {"subject": "Russia", "predicate": "provides", "object": "weapons to Iran", "confidence": 0.7, "negated": false, "hedged": true, "qualifiers": {"time": "since at least 2015", "location": "", "condition": "", "manner": "", "source_attribution": "according to intelligence reports"}}
]}

### Example 2 -- Denial and criticism
Input: "[SPEAKER_01] The White House denied any involvement. But Senator Cruz called the response inadequate."
Output:
{"assertions": [
  {"subject": "The White House", "predicate": "denies", "object": "any involvement", "confidence": 1.0, "negated": false, "hedged": false, "qualifiers": {"time": "", "location": "", "condition": "", "manner": "", "source_attribution": ""}},
  {"subject": "Senator Cruz", "predicate": "criticizes", "object": "the response", "confidence": 0.9, "negated": false, "hedged": false, "qualifiers": {"time": "", "location": "", "condition": "", "manner": "", "source_attribution": ""}}
]}

### Anti-patterns (do NOT produce these)
- {"subject": "SPEAKER_00", "predicate": "states", "object": "SPEAKER_00"} -- SELF-REFERENTIAL
- {"subject": "he", "predicate": "claims", "object": "that"} -- PRONOUN: resolve to entity names
- {"subject": "SPEAKER_01", "predicate": "is", "object": "talking"} -- VAGUE predicate
- {"subject": "SPEAKER_00", "predicate": "states", "object": "I want to get someone on the show"} -- META-COMMENTARY

## Rules

1. No self-referential triples: subject and object must differ.
2. No pronouns: resolve to entity names. Use SPEAKER_XX if real name unknown.
3. Skip meta-commentary and conversational filler.
4. Skip vague predicates: "is", "are", "was", "has", "got".
5. Each assertion must be independently meaningful.
6. Preserve speaker attribution in source_attribution qualifier.""",
)

# Speech-act predicates for media content, with bridges to congress/leaks vocabularies
MEDIA_PREDICATE_MAPPINGS = {
    # Speech acts
    "said": "states",
    "says": "states",
    "stated": "states",
    "claimed": "claims",
    "alleges": "claims",
    "alleged": "claims",
    "denied": "denies",
    "confirmed": "confirms",
    "acknowledged": "acknowledges",
    "admitted": "acknowledges",
    "questioned": "questions",
    "asked": "questions",
    "responded": "responds_to",
    "replied": "responds_to",
    "referenced": "references",
    "mentioned": "references",
    "cited": "references",
    "discussed": "discusses",
    "talked about": "discusses",
    "criticized": "criticizes",
    "condemned": "criticizes",
    "attacked": "criticizes",
    # Bridge to congress vocabulary
    "endorsed": "supports",
    "supported": "supports",
    "backed": "supports",
    "opposed": "opposes",
    "rejected": "opposes",
    # Bridge to leaks vocabulary
    "owns": "owns",
    "transferred": "transfers",
    "operates": "operates",
    # General
    "is a member of": "member_of",
    "works for": "works_for",
    "works at": "works_for",
    "leads": "leads",
    "founded": "founded",
    "created": "created",
    # Filter vague predicates (mapped to empty string = skip)
    "is": "",
    "are": "",
    "was": "",
    "were": "",
    "have": "",
    "has": "",
    "had": "",
    "got": "",
    "came": "",
    "went": "",
    # Additional normalizations
    "left": "departed",
    "spiked": "increased",
    "provided": "provides",
    "rose": "increased",
    "fell": "decreased",
    "dropped": "decreased",
}


@asset(
    group_name="media_ingest",
    description="Extract qualified assertions from one document's transcription chunks via LLM",
    compute_kind="llm",
    code_version=_CODE_VERSION,
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=LLM_ASSET_K8S_CONFIG,
)
def media_assertions(
    context: AssetExecutionContext,
    media_chunks: list[TextChunk],
) -> Output[list[Assertion]]:
    partition_key = context.partition_key
    context.log.info(
        f"Starting media_assertions extraction for partition={partition_key}, chunk_count={len(media_chunks)}"
    )
    with trace_operation(
        "media_assertions",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
            "chunk_count": len(media_chunks),
        },
    ):
        logger.info(
            "Starting media_assertions extraction for partition=%s (%d chunks)",
            partition_key,
            len(media_chunks),
        )

        if not media_chunks:
            context.log.info(f"No chunks for partition={partition_key} — returning empty assertions")
            return Output(
                [],
                metadata={
                    "document_id": partition_key,
                    "assertion_count": 0,
                    "negated_count": 0,
                    "hedged_count": 0,
                },
            )

        context.log.info(f"Received {len(media_chunks)} chunks from upstream for LLM extraction")
        llm_start = time.monotonic()
        _, all_assertions = extract_validated(
            media_chunks,
            code_location="media_ingest",
            max_concurrency=5,
        )
        llm_elapsed = time.monotonic() - llm_start

        negated_count = sum(1 for a in all_assertions if a.negated)
        hedged_count = sum(1 for a in all_assertions if a.hedged)
        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_assertions", layer="gold").inc(
            len(all_assertions)
        )
        context.log.info(
            f"LLM extraction complete in {llm_elapsed:.1f}s: {len(all_assertions)} assertions "
            f"from {len(media_chunks)} chunks ({len(all_assertions) / max(len(media_chunks), 1):.1f} assertions/chunk)"
        )
        context.log.info(
            f"Assertion breakdown: negated={negated_count}, hedged={hedged_count}, "
            f"straightforward={len(all_assertions) - negated_count - hedged_count}"
        )
        return Output(
            all_assertions,
            metadata={
                "document_id": partition_key,
                "assertion_count": len(all_assertions),
                "negated_count": negated_count,
                "hedged_count": hedged_count,
                "chunk_count": len(media_chunks),
            },
        )
