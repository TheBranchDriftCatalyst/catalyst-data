# catalyst-contracts-core

Shared enums and base types for the catalyst knowledge graph contract system.

This is the foundation package — both [`catalyst-llm-contract-mcp`](../catalyst-llm-contract-mcp/) and [`catalyst-langgraph-aio`](../catalyst-langgraph-aio/) depend on it indirectly, and [`dagster-io`](../dagster-io/) re-exports its types for backward compatibility.

## Installation

```bash
uv add catalyst-contracts-core
```

Or as a local editable dependency in `pyproject.toml`:

```toml
[tool.uv.sources]
catalyst-contracts-core = { path = "../catalyst-contracts-core", editable = true }
```

## What's Inside

### Enums (`enums.py`)

All enums are `str` subclasses — they serialize naturally to JSON and compare equal to their string values.

| Enum | Values | Used for |
|------|--------|----------|
| `MentionType` | `PERSON`, `ORG`, `GPE`, `LOC`, `DATE`, `LAW`, `EVENT`, `MONEY`, `NORP`, `FACILITY`, `OTHER` | Entity type classification |
| `AlignmentType` | `sameAs`, `possibleSameAs`, `relatedTo`, `partOf` | Entity resolution / concordance |
| `ExtractionMethod` | `llm`, `spacy`, `regex`, `manual`, `structured` | Provenance tracking |

```python
from catalyst_contracts_core import MentionType

assert MentionType.ORG == "ORG"
assert isinstance(MentionType.ORG, str)
```

### Base Types (`types.py`)

| Model | Fields | Purpose |
|-------|--------|---------|
| `Provenance` | `source_document_id`, `chunk_id`, `confidence` (0.0–1.0), `extraction_method`, `timestamp`, `span_start/end`, `extraction_model`, `code_location` | Tracks how and where an extraction was produced |

```python
from catalyst_contracts_core import Provenance, ExtractionMethod

p = Provenance(
    source_document_id="doc-001",
    chunk_id="chunk-01",
    confidence=0.95,
    extraction_method=ExtractionMethod.LLM,
)
assert p.timestamp  # auto-generated ISO-8601
assert p.span_start is None  # optional
```

## Package Structure

```
src/catalyst_contracts_core/
    __init__.py          # Re-exports all public symbols
    enums.py             # MentionType, AlignmentType, ExtractionMethod
    types.py             # Provenance base model
```

## Dependency Graph

```
catalyst-contracts-core          <-- you are here
    |
    +-- catalyst-llm-contract-mcp    (validators use MentionType in rules)
    +-- dagster-io                   (backward-compatible re-exports)
```

## Testing

```bash
uv run pytest -v                                              # 20 tests
uv run pytest --cov=src/catalyst_contracts_core --cov-report=term-missing  # 100% coverage
```

See [TESTING.md](../catalyst-llm-contract-mcp/TESTING.md) and [TESTING.md](../catalyst-langgraph-aio/TESTING.md) for the downstream packages' test documentation.

## Related Packages

| Package | Purpose | Link |
|---------|---------|------|
| [`catalyst-llm-contract-mcp`](../catalyst-llm-contract-mcp/) | MCP server that validates LLM outputs using these types | [README](../catalyst-llm-contract-mcp/README.md) |
| [`catalyst-langgraph-aio`](../catalyst-langgraph-aio/) | LangGraph orchestration that calls the MCP server | [README](../catalyst-langgraph-aio/README.md) |
| [`dagster-io`](../dagster-io/) | Dagster pipeline that re-exports these types | — |
