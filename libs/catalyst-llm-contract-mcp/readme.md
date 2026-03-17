# catalyst-llm-contract-mcp

MCP (Model Context Protocol) server that enforces structural and contextual contracts on LLM outputs before they enter the knowledge graph. Acts as a **trust boundary** between non-deterministic LLM extraction and deterministic KG persistence.

## Why This Exists

LLMs produce mentions, propositions, and spatial groundings — but their output is non-deterministic and frequently contains:
- Span offsets that don't align with source text
- Entity types that don't exist in the ontology
- Confidence scores outside [0, 1]
- Proposition references to non-existent mention IDs
- Duplicate extractions

This server validates every extraction before it reaches the knowledge graph, rejects bad data with typed error codes, and generates machine-readable repair instructions so the LLM can self-correct.

## Architecture

```
                          Trust Boundary
                               |
  LLM Output ──> [ MCP Server ] ──> Validated Data ──> KG
                    |         |
              Validators   Repair Generator
                    |         |
              Error Codes  RepairPlan
```

This server is consumed by [`catalyst-langgraph-aio`](../catalyst-langgraph-aio/) via either:
- **StdioMCPClient** — subprocess JSON-RPC (production)
- **DirectMCPClient** — in-process Python import (testing/local)

Shared types come from [`catalyst-contracts-core`](../catalyst-contracts-core/).

## Installation

```bash
uv add catalyst-llm-contract-mcp
```

## Running the Server

```bash
# Stdio transport (for MCP client integration)
catalyst-contracts

# Or directly
uv run python -m catalyst_contracts.server
```

## MCP Tools

The server exposes 7 tools via the MCP protocol:

| Tool | Input | Output | Purpose |
|------|-------|--------|---------|
| `get_contract_schemas` | — | JSON schemas for all models | Schema introspection |
| `validate_mentions_tool` | mentions, source_text, document_id | `ValidationResult` | Span alignment, type checking, confidence bounds, dedup |
| `validate_propositions_tool` | propositions, known_mention_ids, source_text | `ValidationResult` | Reference integrity, predicate format, confidence bounds |
| `validate_spatial_grounding` | candidates, source_text | `ValidationResult` | Coordinate bounds, H3 precision, WKT geometry |
| `validate_math_propositions` | propositions | `ValidationResult` | Statement validation, object kinds, symbol checks |
| `validate_concordance_candidates` | candidate_sets, known_entity_ids | `ValidationResult` | Entity existence, score bounds, consistency |
| `generate_repair_instructions` | validation_result, original_payload | `RepairPlan` | Maps errors to actionable repair instructions |

### Validation Result Shape

Every validator returns a `ValidationResult`:

```json
{
  "verdict": "valid | invalid | ambiguous",
  "valid_count": 3,
  "invalid_count": 1,
  "warning_count": 0,
  "errors": [
    {
      "code": "SPAN_MISMATCH",
      "message": "Text at span [0:14] is 'The United Nat' not 'United Nations'",
      "severity": "error",
      "path": "mentions[0].span_start",
      "context": { "expected": "United Nations", "got": "The United Nat" }
    }
  ]
}
```

### Error Codes

| Code | Severity | Validator(s) | Meaning |
|------|----------|-------------|---------|
| `SPAN_MISMATCH` | error | mentions | Text at given offsets doesn't match surface form |
| `INVALID_TYPE` | error | mentions, math | Entity/proposition type not in ontology |
| `CONFIDENCE_OUT_OF_RANGE` | error | mentions, propositions, spatial | Confidence not in [0, 1] |
| `DUPLICATE_SPAN` | error | mentions | Same span range appears twice |
| `INVALID_REFERENCE` | error | propositions | mention_id not in known set |
| `INVALID_PREDICATE` | warning | propositions | Predicate not in snake_case |
| `COORDINATE_OUT_OF_RANGE` | error | spatial | Lat/lon outside valid bounds |
| `INVALID_GEOMETRY` | error | spatial | WKT string malformed |
| `H3_PRECISION_EXCEEDED` | warning | spatial | H3 resolution exceeds max supported |
| `UNKNOWN_ENTITY` | error | concordance | Entity ID not in known set |
| `SCORE_OUT_OF_RANGE` | error | concordance | Score outside [0, 1] |
| `INCONSISTENT_SCORES` | warning | concordance | Combined score inconsistent with sub-scores |

### Repair Instructions

The repair generator maps each error code to a `RepairInstruction`:

```json
{
  "instructions": [
    {
      "action": "replace",
      "path": "mentions[0].span_start",
      "current_value": "0",
      "suggested_value": "4",
      "reason": "Span does not align with source text",
      "auto_applicable": true
    }
  ],
  "preserves_valid_fields": true
}
```

## Package Structure

```
src/catalyst_contracts/
    server.py                         # FastMCP server (7 tools)
    models/
        __init__.py                   # Re-exports all 24 model classes
        evidence.py                   # EvidenceSpan, ExtractionIssue, IssueCode, IssueSeverity
        validation.py                 # ValidationResult, ValidationVerdict, ValidationErrorItem
        mentions.py                   # MentionExtraction
        propositions.py               # BinaryProposition, NaryProposition (discriminated union)
        spatial.py                    # SpatialGroundingCandidate
        math.py                       # MathObject, MathProposition, MathPropositionKind
        concordance.py                # ConcordanceCandidateScore, ConcordanceCandidateSet
        repair.py                     # RepairAction, RepairInstruction, RepairPlan
    validators/
        __init__.py                   # Re-exports all 6 validator functions
        mention_validator.py          # validate_mentions()
        proposition_validator.py      # validate_propositions()
        spatial_validator.py          # validate_spatial()
        math_validator.py             # validate_math()
        concordance_validator.py      # validate_concordance()
        repair_generator.py           # generate_repair_plan()
    audit/
        repository.py                 # JSONL append-only audit trail
```

## Audit Trail

Every validation call is automatically recorded to a JSONL audit log at `~/.catalyst/contract-audit.jsonl`:

```json
{"timestamp": "2025-01-15T10:30:00Z", "tool_name": "validate_mentions", "verdict": "valid", "payload_hash": "a1b2c3", "error_count": 0, "accepted": true}
```

## Using Validators Directly (No MCP)

The validators can be imported and called as plain Python functions:

```python
from catalyst_contracts.validators import validate_mentions, generate_repair_plan

result = validate_mentions(mentions, source_text, document_id)
if result.verdict.value == "invalid":
    plan = generate_repair_plan(result, {"mentions": mentions})
    for instruction in plan.instructions:
        print(f"{instruction.action}: {instruction.path} -> {instruction.suggested_value}")
```

## Testing

```bash
uv run pytest -v                                              # 117 tests
uv run pytest --cov=src/catalyst_contracts --cov-report=term-missing  # 99% coverage
```

See [TESTING.md](./TESTING.md) for the full test architecture documentation.

## Related Packages

| Package | Relationship | Link |
|---------|-------------|------|
| [`catalyst-contracts-core`](../catalyst-contracts-core/) | Shared enums (MentionType, etc.) | [README](../catalyst-contracts-core/README.md) |
| [`catalyst-langgraph-aio`](../catalyst-langgraph-aio/) | Orchestration layer that calls this server | [README](../catalyst-langgraph-aio/README.md) |
