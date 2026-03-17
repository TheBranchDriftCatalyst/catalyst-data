# Testing — catalyst-llm-contract-mcp

## Quick Start

```bash
# Run all tests
uv run pytest -v

# Run with coverage
uv run pytest --cov=src/catalyst_contracts --cov-report=term-missing

# Run a specific test layer
uv run pytest tests/test_models/ -v        # Model unit tests
uv run pytest tests/test_validators/ -v    # Validator unit tests
uv run pytest tests/test_server.py -v      # Server integration tests
```

## Test Pyramid

```
            /\
           /  \       14 tests   Server tool integration
          /Srvr \                (test_server.py)
         /-------\
        /         \   5 tests    Audit repository
       /  Audit    \             (test_audit.py)
      /-------------\
     /               \  98 tests  Unit: models + validators
    /   Unit (Model   \
   /    + Validator)   \
  /---------------------\
```

| Layer | Files | Tests | What it covers |
|-------|-------|-------|----------------|
| **Unit: Models** | 8 files | 34 | Pydantic model construction, validation, boundaries, discriminators |
| **Unit: Validators** | 6 files | 49 | Validation logic: error codes, boundary values, edge cases |
| **Unit: Audit** | 1 file | 5 | JSONL append-only persistence, deterministic hashing |
| **Integration: Server** | 1 file | 14 | FastMCP tool functions end-to-end, negative paths, audit isolation |

## Test Files

### Model Tests (`tests/test_models/`)

| File | Tests | Covers |
|------|-------|--------|
| `test_evidence.py` | 6 | `EvidenceSpan` construction, span_end > span_start, text length match, optional fields. `ExtractionIssue` creation with path |
| `test_validation.py` | 4 | `ValidationVerdict` enum values, `ValidationResult` valid/invalid states, `ValidationErrorItem` with context |
| `test_mentions.py` | 5 | `MentionExtraction` construction, confidence bounds (-0.1, 1.1 rejected; 0.0, 1.0 accepted), optional context |
| `test_propositions.py` | 7 | `BinaryProposition` defaults + bounds, `NaryProposition` construction + bounds, `PropositionExtraction` discriminator dispatch |
| `test_spatial.py` | 4 | `SpatialGroundingCandidate` lat/lon bounds (-90/90, -180/180), optional H3/WKT fields |
| `test_math.py` | 4 | `MathObject` full/minimal, `MathProposition` valid construction, all `MathPropositionKind` values |
| `test_concordance.py` | 5 | `ConcordanceCandidateScore` bounds (negative, >1.0), `ConcordanceCandidateSet` valid/empty |
| `test_repair.py` | 4 | `RepairInstruction` REPLACE/DELETE actions, `RepairPlan` empty/with-instructions |

### Validator Tests (`tests/test_validators/`)

| File | Tests | Covers |
|------|-------|--------|
| `test_mention_validator.py` | 13 | Valid/invalid mentions, span mismatch, invalid type, confidence OOR, duplicate spans, mixed valid/invalid (AMBIGUOUS), empty list, evidence warning, boundary 0.0/1.0, invalid span range, span exceeds source |
| `test_proposition_validator.py` | 8 | Valid propositions, invalid subject/object/nary reference, non-snake predicate warning, confidence OOR, empty list, mixed valid/invalid (AMBIGUOUS) |
| `test_spatial_validator.py` | 11 | Valid candidates, lat/lon OOR, invalid/valid WKT, confidence OOR, empty list, boundary lat +-90 / lon +-180, H3 precision exceeded, H3 invalid hex, mixed valid/invalid (AMBIGUOUS) |
| `test_math_validator.py` | 6 | Valid proposition, invalid kind, empty statement, invalid object kind, empty symbol, empty list |
| `test_concordance_validator.py` | 5 | Valid set, unknown entity, score OOR, inconsistent combined warning, empty sets |
| `test_repair_generator.py` | 13 | Empty errors, span mismatch repair, confidence clamp, duplicate span delete, invalid type coerce, invalid reference delete, coordinate OOR coerce, score OOR numeric/non-numeric, unknown entity delete, unknown code fallback, `_resolve_path` non-dict edge case |

### Audit Tests

| File | Tests | Covers |
|------|-------|--------|
| `test_audit.py` | 5 | Record + read roundtrip, append-only semantics, empty read, deterministic payload hash, parent directory creation |

### Server Integration Tests

| File | Tests | Covers |
|------|-------|--------|
| `test_server.py` | 14 | All 7 MCP tool functions (happy path), 5 empty-list negative paths, audit record verification. Uses `autouse` fixture to isolate audit writes to `tmp_path` |

## Architecture: How Tests Work

### Test Isolation

The MCP server module instantiates `audit = AuditRepository()` at module level, which defaults to `~/.catalyst/contract-audit.jsonl`. To prevent filesystem side effects, `test_server.py` uses an `autouse` fixture:

```python
@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    import catalyst_contracts.server as srv
    monkeypatch.setattr(srv, "audit", AuditRepository(tmp_path / "audit.jsonl"))
```

### Fixture-Driven Validation Tests

Validator tests load fixture JSON files from `tests/fixtures/`:
- `source_text.txt` — reference document for span alignment
- `valid_mentions.json` / `invalid_mentions.json`
- `valid_propositions.json` / `invalid_propositions.json`

The `conftest.py` provides these as pytest fixtures:

```python
@pytest.fixture
def valid_mentions_data():
    return json.loads((FIXTURES / "valid_mentions.json").read_text())
```

### Error Code Coverage

Each validator returns a `ValidationResult` with typed `ValidationErrorItem` entries using `IssueCode` enums (`SPAN_MISMATCH`, `INVALID_TYPE`, `CONFIDENCE_OUT_OF_RANGE`, etc.). Tests assert both the verdict and the specific error codes:

```python
result = validate_mentions(bad, source_text, "doc-1")
assert result.verdict.value == "invalid"
assert any(e.code == "SPAN_MISMATCH" for e in result.errors)
```

### Repair Generator Testing

The repair generator maps `IssueCode` → `RepairInstruction`. Tests cover every branch:

| IssueCode | RepairAction | auto_applicable |
|-----------|-------------|-----------------|
| `SPAN_MISMATCH` | REPLACE | True |
| `CONFIDENCE_OUT_OF_RANGE` | COERCE (clamp to 0.0–1.0) | True |
| `DUPLICATE_SPAN` | DELETE | True |
| `INVALID_TYPE` | COERCE | False |
| `INVALID_REFERENCE` | DELETE | False |
| `COORDINATE_OUT_OF_RANGE` | COERCE | True |
| `SCORE_OUT_OF_RANGE` | COERCE (numeric) / REPLACE (non-numeric) | True / False |
| `UNKNOWN_ENTITY` | DELETE | False |
| Unknown code | REPLACE (generic) | False |

## Coverage

```
Module                                   Stmts   Miss   Cover
--------------------------------------------------------------
models/ (all 8)                           152      0    100%
validators/mention_validator.py            53      0    100%
validators/proposition_validator.py        44      0    100%
validators/spatial_validator.py            49      0    100%
validators/repair_generator.py             53      0    100%
validators/math_validator.py               39      1     97%
validators/concordance_validator.py        42      1     98%
audit/repository.py                        31      1     97%
server.py                                  58      2     97%
--------------------------------------------------------------
TOTAL                                     561      5     99%
```

**Intentionally uncovered (5 lines):**
- `server.py:139, 143` — `main()` entrypoint (calls `mcp.run()`)
- `audit/repository.py:21` — datetime formatting edge case
- `math_validator.py:87`, `concordance_validator.py:92` — final unreachable return statements

## Fixtures Directory

```
tests/fixtures/
  source_text.txt              Source document for span validation
  valid_mentions.json          3 correctly-aligned mentions
  invalid_mentions.json        2 mentions with wrong spans/types
  valid_propositions.json      2 valid binary propositions
  invalid_propositions.json    Propositions with bad references
```

## Adding New Tests

1. **New model** → Add `tests/test_models/test_<name>.py` testing construction, bounds, optional fields
2. **New validator** → Add `tests/test_validators/test_<name>_validator.py` testing valid/invalid/boundary/empty inputs
3. **New IssueCode** → Add a repair generator test in `test_repair_generator.py`
4. **New server tool** → Add to `TestServerTools` in `test_server.py` + empty-list negative case in `TestServerToolsNegativePaths`
5. Always verify: `uv run pytest --cov=src/catalyst_contracts --cov-report=term-missing`
