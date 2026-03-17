# libs/ — LLM Contract Validation System

Three packages that implement a **trust boundary** between LLM extraction and knowledge graph persistence. LLM outputs are validated against structural contracts, rejected with typed error codes, repaired via feedback loops, and only persisted once they pass all checks.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          catalyst-langgraph-aio                            │
│                                                                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │ extract  │──>│ validate │──>│ extract  │──>│ validate │──>│ persist  │ │
│  │ mentions │   │ mentions │   │ proposit.│   │ proposit.│   │ artifacts│ │
│  └──────────┘   └────┬─────┘   └──────────┘   └────┬─────┘   └──────────┘ │
│       LLM            │ MCP          LLM             │ MCP         Repo     │
│                 ┌────┴─────┐                   ┌────┴─────┐                │
│                 │  repair  │                   │  repair  │                │
│                 │ mentions │                   │ proposit.│                │
│                 └──────────┘                   └──────────┘                │
│                      LLM                            LLM                    │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │ catalyst-llm-contract  │
                    │        -mcp            │
                    │                        │
                    │  7 MCP validation tools │
                    │  6 validators           │
                    │  12 error codes         │
                    │  Repair generator       │
                    │  JSONL audit trail      │
                    └───────────┬────────────┘
                                │
                    ┌───────────┴───────────┐
                    │ catalyst-contracts    │
                    │        -core          │
                    │                        │
                    │  MentionType (11)      │
                    │  AlignmentType (4)     │
                    │  ExtractionMethod (5)  │
                    │  Provenance model      │
                    └───────────────────────┘
```

## Packages

| Package | Purpose | Tests | Coverage |
|---------|---------|-------|----------|
| [`catalyst-contracts-core`](./catalyst-contracts-core/) | Shared enums and base types | 20 | 100% |
| [`catalyst-llm-contract-mcp`](./catalyst-llm-contract-mcp/) | MCP server — validates LLM outputs | 117 | 99% |
| [`catalyst-langgraph-aio`](./catalyst-langgraph-aio/) | LangGraph — extract/validate/repair orchestration | 61 | 93% |
| **Total** | | **198** | **97%** |

## Dependency Graph

```
catalyst-contracts-core
    ^           ^
    |           |
    |     catalyst-llm-contract-mcp
    |
dagster-io (backward-compatible re-exports)

catalyst-langgraph-aio ──calls──> catalyst-llm-contract-mcp (via MCP protocol)
```

`catalyst-langgraph-aio` does **not** have a Python dependency on `catalyst-llm-contract-mcp` — it communicates via the MCP protocol (stdio JSON-RPC in production, or `DirectMCPClient` for in-process testing).

## Data Flow

```
Source Text
    │
    ▼
1. LLM extracts mentions ──> [{text: "CERN", type: "ORG", span_start: 42, ...}]
    │
    ▼
2. MCP validates ──> {verdict: "invalid", errors: [{code: "SPAN_MISMATCH", ...}]}
    │
    ▼
3. Repair generator ──> {instructions: [{action: "replace", path: "span_start", ...}]}
    │
    ▼
4. LLM repairs with error context ──> [{text: "CERN", span_start: 45, ...}]
    │
    ▼
5. MCP re-validates ──> {verdict: "valid"}
    │
    ▼
6. Repeat for propositions
    │
    ▼
7. Persist to JSONL ──> mentions.jsonl, propositions.jsonl, audit_trail.jsonl
```

## Running Tests

```bash
# Individual packages
cd libs/catalyst-contracts-core   && uv run pytest -v
cd libs/catalyst-llm-contract-mcp && uv run pytest -v
cd libs/catalyst-langgraph-aio    && uv run pytest -v

# With coverage
cd libs/catalyst-contracts-core   && uv run pytest --cov=src/catalyst_contracts_core --cov-report=term-missing
cd libs/catalyst-llm-contract-mcp && uv run pytest --cov=src/catalyst_contracts --cov-report=term-missing
cd libs/catalyst-langgraph-aio    && uv run pytest --cov=src/catalyst_langgraph --cov-report=term-missing
```

## Documentation Index

| Document | Location | Content |
|----------|----------|---------|
| This file | `libs/README.md` | System overview, architecture, data flow |
| Core README | [`catalyst-contracts-core/README.md`](./catalyst-contracts-core/README.md) | Enum reference, Provenance model, usage |
| MCP README | [`catalyst-llm-contract-mcp/README.md`](./catalyst-llm-contract-mcp/README.md) | Tool reference, error codes, repair instructions, audit |
| LangGraph README | [`catalyst-langgraph-aio/README.md`](./catalyst-langgraph-aio/README.md) | Graph topology, state machine, DI, prompt system |
| MCP Testing | [`catalyst-llm-contract-mcp/TESTING.md`](./catalyst-llm-contract-mcp/TESTING.md) | Test pyramid, coverage gaps, fixture reference |
| LangGraph Testing | [`catalyst-langgraph-aio/TESTING.md`](./catalyst-langgraph-aio/TESTING.md) | Test pyramid, mock architecture, coverage gaps |

## Key Design Decisions

**Why MCP instead of direct function calls?**
The MCP protocol decouples the orchestration layer from the validation layer. In production, the MCP server runs as a separate process — it can be versioned, deployed, and scaled independently. In tests, `DirectMCPClient` and `MockMCPClient` avoid the subprocess overhead.

**Why repair loops instead of re-prompting from scratch?**
Feeding specific error codes and repair instructions back to the LLM is more token-efficient and produces better results than blind re-extraction. The LLM sees exactly what went wrong (e.g., "span [0:14] doesn't match source text") and can make targeted fixes.

**Why JSONL for persistence?**
Append-only JSONL is the simplest durable format for the bronze/silver layer of the medallion architecture. It's human-readable, git-friendly, and trivially parseable. The downstream Dagster pipeline can promote validated JSONL to structured tables (gold layer).

**Why TypedDict instead of Pydantic for graph state?**
LangGraph's `StateGraph` works natively with TypedDict. Using Pydantic for graph state would add serialization overhead on every node transition. Pydantic is used at the boundaries (MCP models, validation results) where schema enforcement matters.
