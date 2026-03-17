# catalyst-langgraph-aio

Async LangGraph orchestration for LLM extraction with MCP contract validation. Implements the extract-validate-repair loop that sits between raw LLM output and the knowledge graph.

## Why This Exists

LLM extraction is non-deterministic. A single prompt can produce mentions with wrong spans, propositions with bad references, or confidence scores outside valid ranges. Rather than hoping the LLM gets it right, this package builds a **stateful workflow** that:

1. Extracts mentions/propositions via LLM
2. Validates them against contracts (via MCP server)
3. If rejected: generates a repair plan, feeds errors back to the LLM, and retries
4. If accepted (or retries exhausted): persists validated artifacts

## Graph Topology

```
START
  |
  v
extract_mentions ──> validate_mentions ──┬──> extract_propositions
                          |              |
                    [rejected]      [accepted]
                          |
                          v
                    repair_mentions ──> validate_mentions (loop)
                                              |
                                        [max retries]
                                              |
                                              v
                                             END

extract_propositions ──> validate_propositions ──┬──> persist_artifacts ──> END
                               |                 |
                         [rejected]          [accepted]
                               |
                               v
                         repair_propositions ──> validate_propositions (loop)
                                                       |
                                                 [max retries]
                                                       |
                                                       v
                                                      END
```

Each validation node has three possible outcomes:
- **accepted** — proceed to next stage
- **rejected + retries left** — repair and re-validate
- **rejected + max retries** — end gracefully (nothing persisted)

## Installation

```bash
uv add catalyst-langgraph-aio
```

## Quick Start

```python
from catalyst_langgraph import build_extraction_graph
from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.clients.mcp import StdioMCPClient
from catalyst_langgraph.repository.jsonl import JsonlRepository

# Wire up dependencies
llm = LLMClient(model="gpt-4o-mini")
mcp = StdioMCPClient(["catalyst-contracts"])  # MCP server subprocess
repo = JsonlRepository("/data/extractions")

# Build the graph
graph = build_extraction_graph(llm, mcp, repo)

# Run extraction
await mcp.start()
result = await graph.ainvoke({
    "source_metadata": {
        "document_id": "doc-001",
        "chunk_id": "chunk-01",
        "source": "arxiv",
        "domain": "physics",
    },
    "raw_text": "The experiment was conducted at CERN in Geneva...",
    "max_retries": 3,
})
await mcp.stop()

print(result["status"])              # "completed"
print(result["accepted_mentions"])   # validated mention dicts
print(result["accepted_propositions"])  # validated proposition dicts
```

## Dependency Injection

The graph is constructed via `build_extraction_graph(llm_client, mcp_client, repository)`. All three are abstract interfaces — swap implementations for testing or different environments:

### LLM Client

| Implementation | Use case |
|---------------|----------|
| `LLMClient` | Production — wraps `ChatOpenAI`, configured via env vars |
| `MockLLMClient` (in tests) | Returns canned JSON responses |

Environment variables:
- `LLM_MODEL` (default: `gpt-4o-mini`)
- `LLM_BASE_URL` (default: `https://api.openai.com/v1`)
- `LLM_API_KEY` / `OPENAI_API_KEY`
- `LLM_TEMPERATURE` (default: `0.0`)
- `LLM_MAX_TOKENS` (default: `4096`)

### MCP Client

| Implementation | Use case |
|---------------|----------|
| `StdioMCPClient` | Production — spawns MCP server as subprocess, communicates via JSON-RPC over stdio |
| `DirectMCPClient` | Integration testing — imports Python validator functions directly, no subprocess |
| `MockMCPClient` | Unit testing — returns configurable canned responses |

```python
# Production: subprocess
mcp = StdioMCPClient(["catalyst-contracts"])

# Testing: direct import
from catalyst_contracts.validators.mention_validator import validate_mentions
class MyHandler:
    def validate_mentions(self, mentions, **kwargs):
        return validate_mentions(mentions, kwargs["source_text"], kwargs["doc_id"]).model_dump()
mcp = DirectMCPClient(MyHandler())

# Unit tests: mock
mcp = MockMCPClient({"validate_mentions": {"verdict": "accepted", "errors": []}})
```

### Repository

| Implementation | Use case |
|---------------|----------|
| `JsonlRepository` | Default — append-only JSONL files under `<base_path>/<document_id>/` |
| Custom `ArtifactRepository` subclass | S3, database, etc. |

Output structure:
```
<base_path>/
    <document_id>/
        mentions.jsonl
        propositions.jsonl
        audit_trail.jsonl
```

## State Machine

The graph operates on an `ExtractionState` TypedDict:

| Field | Type | Purpose |
|-------|------|---------|
| `source_metadata` | dict | Document/chunk IDs, source, domain |
| `raw_text` | str | Source text for extraction |
| `current_mention_candidates` | list[dict] | Latest LLM mention output |
| `current_proposition_candidates` | list[dict] | Latest LLM proposition output |
| `accepted_mentions` | list[dict] | Validated mentions (set after accepted) |
| `accepted_propositions` | list[dict] | Validated propositions (set after accepted) |
| `latest_mention_validation` | dict | Most recent MCP validation result |
| `latest_proposition_validation` | dict | Most recent MCP validation result |
| `latest_repair_plan` | dict | Most recent repair instructions |
| `mention_retry_count` | int | Number of mention repair attempts |
| `proposition_retry_count` | int | Number of proposition repair attempts |
| `max_retries` | int | Maximum repair attempts before giving up |
| `status` | str | Current `WorkflowStatus` value |
| `audit_events` | list[dict] | Chronological audit trail |
| `error` | str | Error message if failed |

## Prompt System

Prompts are loaded from `.prompt` files with optional YAML frontmatter:

```yaml
---
model: gpt-4o
temperature: 0.1
max_tokens: 2048
metadata:
  version: "1.0"
---
Extract all named entity mentions from the following text.
Return JSON: {"mentions": [...]}
```

Set `PROMPT_REGISTRY_DIR` to override built-in prompts with custom versions.

## Package Structure

```
src/catalyst_langgraph/
    __init__.py                # Exports: build_extraction_graph, ExtractionState, WorkflowStatus
    graph.py                   # StateGraph construction + routing functions
    state.py                   # ExtractionState TypedDict, WorkflowStatus enum, AuditEvent
    prompts.py                 # YAML frontmatter prompt loader
    clients/
        __init__.py            # Exports: LLMClient, MCPClient, DirectMCPClient, MockMCPClient
        llm.py                 # LLMClient (wraps ChatOpenAI)
        mcp.py                 # MCPClient ABC + StdioMCPClient, DirectMCPClient, MockMCPClient
    nodes/
        extract_mentions.py    # LLM extraction → mention candidates
        validate_mentions.py   # MCP validation of mentions
        repair_mentions.py     # Feed errors back to LLM for repair
        extract_propositions.py  # LLM extraction → proposition candidates
        validate_propositions.py # MCP validation of propositions
        repair_propositions.py   # Feed errors back to LLM for repair
        persist_artifacts.py     # Write validated data to repository
    repository/
        base.py                # ArtifactRepository ABC
        jsonl.py               # JsonlRepository implementation
```

## Nodes

Each node is a factory function `make_<name>(dependency)` that returns an async function. The factory pattern allows dependency injection without globals:

| Node | Dependency | Input State | Output State |
|------|-----------|-------------|--------------|
| `extract_mentions` | LLMClient | raw_text | current_mention_candidates |
| `validate_mentions` | MCPClient | current_mention_candidates | latest_mention_validation, accepted_mentions |
| `repair_mentions` | LLMClient | latest_mention_validation, current_mention_candidates | current_mention_candidates, mention_retry_count++ |
| `extract_propositions` | LLMClient | raw_text, accepted_mentions | current_proposition_candidates |
| `validate_propositions` | MCPClient | current_proposition_candidates | latest_proposition_validation, accepted_propositions |
| `repair_propositions` | LLMClient | latest_proposition_validation, current_proposition_candidates | current_proposition_candidates, proposition_retry_count++ |
| `persist_artifacts` | Repository | accepted_mentions, accepted_propositions, audit_events | status=completed |

## Testing

```bash
uv run pytest -v                                              # 61 tests
uv run pytest --cov=src/catalyst_langgraph --cov-report=term-missing  # 93% coverage
```

See [TESTING.md](./TESTING.md) for the full test pyramid documentation, fixture reference, and architecture.

## Related Packages

| Package | Relationship | Link |
|---------|-------------|------|
| [`catalyst-contracts-core`](../catalyst-contracts-core/) | Shared enums used across the system | [README](../catalyst-contracts-core/README.md) |
| [`catalyst-llm-contract-mcp`](../catalyst-llm-contract-mcp/) | MCP server called by StdioMCPClient/DirectMCPClient | [README](../catalyst-llm-contract-mcp/README.md) |

## System Overview

For the full three-package architecture, see the [system README](../README.md).
