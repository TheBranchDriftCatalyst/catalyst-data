# Testing — catalyst-langgraph-aio

## Quick Start

```bash
# Run all tests
uv run pytest -v

# Run with coverage
uv run pytest --cov=src/catalyst_langgraph --cov-report=term-missing

# Run a specific test file
uv run pytest tests/test_nodes.py -v

# Run a specific test
uv run pytest tests/test_graph_happy_path.py::test_happy_path -v
```

## Test Pyramid

```
            /\
           /  \       3 tests   End-to-end: full graph invocation
          / E2E\                (test_full_flow_integration.py)
         /------\
        /        \    5 tests   Integration: graph path tests
       / Integr.  \             (test_graph_*.py)
      /------------\
     /              \  53 tests  Unit: nodes, routing, clients,
    /     Unit       \           prompts, state, repository
   /------------------\
```

| Layer | Files | Tests | What it covers |
|-------|-------|-------|----------------|
| **Unit** | 8 files | 45 | Individual functions in isolation |
| **Integration** | 3 files | 5 | Graph paths with mock LLM + mock MCP |
| **E2E** | 1 file | 3 | Full pipeline: extract → validate → repair → persist |

## Test Files

### Unit Tests

| File | Tests | Covers |
|------|-------|--------|
| `test_state.py` | 4 | `WorkflowStatus` enum values, `SourceMetadata`, `AuditEvent` defaults, `ExtractionState` TypedDict |
| `test_nodes.py` | 13 | All 7 node functions: extract/validate/repair mentions & propositions, persist. Includes JSON-parse error fallback branches |
| `test_routing.py` | 8 | `_route_after_mention_validation` and `_route_after_proposition_validation`: accepted/rejected/max-retries/missing-data branches |
| `test_mcp_client.py` | 7 | `MockMCPClient` defaults, custom responses, call tracking, callable handlers. `DirectMCPClient` sync/async dispatch, missing method error |
| `test_direct_mcp_client.py` | 4 | `DirectMCPClient` sync handler, async handler, missing method, kwargs passthrough |
| `test_llm_client.py` | 4 | `LLMClient` constructor: explicit params override env, defaults, API key fallback, env var precedence |
| `test_prompts.py` | 9 | `ParsedPrompt` defaults, YAML frontmatter parsing, load with/without registry, edge cases |
| `test_repository.py` | 4 | `JsonlRepository`: save mentions/propositions/audit, append mode |

### Integration Tests (Graph Paths)

| File | Tests | Covers |
|------|-------|--------|
| `test_graph_happy_path.py` | 1 | Full graph: all validations pass first try → status `completed`, files persisted, both MCP tools called |
| `test_graph_repair_path.py` | 2 | Mention repair loop (fail→repair→pass) and proposition repair loop |
| `test_graph_max_retries.py` | 2 | Mention retries exhausted, proposition retries exhausted — nothing persisted |

### End-to-End Tests

| File | Tests | Covers |
|------|-------|--------|
| `test_full_flow_integration.py` | 3 | Self-contained pipeline with own fixtures: happy path (audit trail verification), repair loop, graceful max-retries failure |

## Architecture: How Tests Work

### Dependency Injection

The graph is constructed via `build_extraction_graph(llm_client, mcp_client, repository)`. Tests swap in:

- **`MockLLMClient`** (conftest.py) — returns canned JSON responses, configurable per-prompt
- **`MockMCPClient`** (from `catalyst_langgraph.clients.mcp`) — returns canned validation verdicts, supports callable handlers for stateful scenarios
- **`JsonlRepository(tmp_path)`** — real repository writing to pytest's temp directory

### Repair Loop Testing

Repair tests use stateful callables that change behavior across calls:

```python
call_count = {"n": 0}
def mention_validator(args):
    call_count["n"] += 1
    if call_count["n"] == 1:
        return {"verdict": "rejected", "errors": ["Missing start_offset"]}
    return {"verdict": "accepted", "errors": []}

mock_mcp.set_response("validate_mentions", mention_validator)
```

This simulates: first validation fails → repair runs → second validation passes.

### Routing Tests

Routing functions are tested as pure functions with hand-crafted state dicts:

```python
state = {
    "latest_mention_validation": {"verdict": "rejected"},
    "mention_retry_count": 0,
    "max_retries": 3,
}
assert _route_after_mention_validation(state) == "repair_mentions"
```

### Node Tests

Each node is tested in isolation by calling `make_<node>(llm, mcp)` or `make_<node>(repo)` with mocks, then invoking the returned async function with a minimal state dict.

## Coverage

```
Module                                   Stmts   Miss   Cover
--------------------------------------------------------------
graph.py                                    52      0    100%
nodes/ (all 7)                             145      0    100%
state.py                                    44      0    100%
prompts.py                                  39      0    100%
repository/jsonl.py                         21      0    100%
clients/mcp.py (DirectMCP + Mock)           62     22     65%
clients/llm.py                              26      8     69%
--------------------------------------------------------------
TOTAL                                      401     30     93%
```

**Intentionally uncovered:**
- `StdioMCPClient` (65%) — subprocess JSON-RPC transport; requires a real MCP server process
- `LLMClient.complete()` / `structured_output()` (69%) — requires real OpenAI API calls

These are infrastructure transport layers tested via integration/smoke tests in deployment, not in unit tests.

## Fixtures (conftest.py)

| Fixture | Type | Description |
|---------|------|-------------|
| `mock_llm` | `MockLLMClient` | Configurable mock LLM with `set_default_mentions()` and `set_default_propositions()` |
| `mock_mcp` | `MockMCPClient` | Mock MCP client with `set_response()` for per-tool control |
| `sample_mentions` | `list[dict]` | 2 mentions: "United Nations" (ORG) and "New York City" (GPE) |
| `sample_propositions` | `list[dict]` | 1 proposition: UN founded_in 1945 |
| `sample_state` | `dict` | Canonical initial `ExtractionState` for graph invocation |

## Adding New Tests

1. **New node** → Add unit test in `test_nodes.py` using mock LLM/MCP
2. **New routing branch** → Add case in `test_routing.py` with hand-crafted state
3. **New graph path** → Add integration test in appropriate `test_graph_*.py`
4. **New client** → Add unit test for the client, mock the transport layer
5. Always verify: `uv run pytest --cov=src/catalyst_langgraph --cov-report=term-missing`
