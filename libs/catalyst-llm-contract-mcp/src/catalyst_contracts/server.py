from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from catalyst_contracts.audit.repository import AuditRepository
from catalyst_contracts.models.validation import ValidationResult
from catalyst_contracts.validators.concordance_validator import validate_concordance
from catalyst_contracts.validators.math_validator import validate_math
from catalyst_contracts.validators.mention_validator import validate_mentions as _validate_mentions
from catalyst_contracts.validators.proposition_validator import validate_propositions as _validate_propositions
from catalyst_contracts.validators.repair_generator import generate_repair_plan
from catalyst_contracts.validators.spatial_validator import validate_spatial

mcp = FastMCP("catalyst-llm-contracts")
audit = AuditRepository()


def _result_to_dict(result: ValidationResult, tool_name: str) -> dict[str, Any]:
    d = result.model_dump(mode="json")
    audit.record(
        tool_name=tool_name,
        verdict=result.verdict.value,
        payload=d,
        error_count=len(result.errors),
        accepted=result.verdict.value == "valid",
    )
    return d


@mcp.tool()
def get_contract_schemas() -> dict[str, Any]:
    """Return JSON schemas for all contract models."""
    from catalyst_contracts.models.concordance import (
        ConcordanceCandidateScore,
        ConcordanceCandidateSet,
    )
    from catalyst_contracts.models.evidence import EvidenceSpan, ExtractionIssue
    from catalyst_contracts.models.math import MathObject, MathProposition
    from catalyst_contracts.models.mentions import MentionExtraction
    from catalyst_contracts.models.propositions import (
        BinaryProposition,
        NaryProposition,
        PropositionExtraction,
    )
    from catalyst_contracts.models.repair import RepairInstruction, RepairPlan
    from catalyst_contracts.models.spatial import SpatialGroundingCandidate
    from catalyst_contracts.models.validation import ValidationResult

    models = [
        EvidenceSpan,
        ExtractionIssue,
        MentionExtraction,
        BinaryProposition,
        NaryProposition,
        PropositionExtraction,
        SpatialGroundingCandidate,
        MathObject,
        MathProposition,
        ConcordanceCandidateScore,
        ConcordanceCandidateSet,
        RepairInstruction,
        RepairPlan,
        ValidationResult,
    ]

    return {
        m.__name__: m.model_json_schema()
        for m in models
    }


@mcp.tool()
def validate_mentions(
    mentions: list[dict],
    source_text: str,
    document_id: str,
) -> dict[str, Any]:
    """Validate a list of mention extractions against the source text."""
    result = _validate_mentions(mentions, source_text, document_id)
    return _result_to_dict(result, "validate_mentions")


@mcp.tool()
def validate_propositions(
    propositions: list[dict],
    known_mention_ids: list[str],
    source_text: str,
) -> dict[str, Any]:
    """Validate a list of propositions against known mention IDs."""
    result = _validate_propositions(
        propositions, set(known_mention_ids), source_text
    )
    return _result_to_dict(result, "validate_propositions")


@mcp.tool()
def validate_spatial_grounding(
    candidates: list[dict],
    source_text: str,
) -> dict[str, Any]:
    """Validate spatial grounding candidates."""
    result = validate_spatial(candidates, source_text)
    return _result_to_dict(result, "validate_spatial_grounding")


@mcp.tool()
def validate_math_propositions(
    propositions: list[dict],
) -> dict[str, Any]:
    """Validate math propositions."""
    result = validate_math(propositions)
    return _result_to_dict(result, "validate_math_propositions")


@mcp.tool()
def validate_concordance_candidates(
    candidate_sets: list[dict],
    known_entity_ids: list[str],
) -> dict[str, Any]:
    """Validate concordance candidate sets against known entity IDs."""
    result = validate_concordance(candidate_sets, set(known_entity_ids))
    return _result_to_dict(result, "validate_concordance_candidates")


@mcp.tool()
def generate_repair_instructions(
    validation_result: dict,
    original_payload: dict,
) -> dict[str, Any]:
    """Generate a repair plan from a validation result."""
    vr = ValidationResult.model_validate(validation_result)
    plan = generate_repair_plan(vr, original_payload)
    return plan.model_dump(mode="json")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
