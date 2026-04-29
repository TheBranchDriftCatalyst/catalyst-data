# catalyst-exgraph

Generic composable extraction graphs with MCP validation, ensemble support, and full provenance.

Replaces the hardcoded NER→SPO pipeline in `catalyst-langgraph-aio` with configurable extract→validate→repair stages that compose into pipelines.

## Quick Start

```python
from catalyst_exgraph import ExtractionResource

# Configure per code location
resource = ExtractionResource(
    ner_model="gliner",              # encoder for fast NER
    spo_model="mistral:latest",      # LLM for propositions
    prompt_dir="k8s/media-ingest/prompts",
)

# Extract mentions (NER only)
result = resource.extract_mentions(chunks, code_location="media_ingest")
mentions = result.mentions  # list[Mention]

# Extract assertions (SPO, using accepted mentions)
result = resource.extract_assertions(
    chunks,
    accepted_mentions=mentions,
    code_location="media_ingest",
)
assertions = result.assertions  # list[Assertion]
```

## Architecture

```
StageConfig           ExtractionResource (Dagster)
    │                        │
    ▼                        ▼
build_stage_graph()    extract_mentions() / extract_assertions()
    │                        │
    ▼                        ▼
┌─────────────────────────────────┐
│  Generic Stage Graph            │
│  extract → validate → repair   │
│  (parameterized by StageConfig) │
└─────────────────────────────────┘
    │                        │
    ▼                        ▼
PipelineBuilder        ConsensusVoter (ensemble)
    │                        │
    ▼                        ▼
NER stage → SPO stage    N models → majority vote
```

## Key Concepts

### StageConfig

Parameterizes one extract→validate→repair loop:

```python
from catalyst_exgraph.config import StageConfig, ner_stage_config, spo_stage_config

# Presets
ner = ner_stage_config(model="gliner", max_retries=0)
spo = spo_stage_config(model="mistral:latest")

# Custom
custom = StageConfig(
    stage_name="custom_ner",
    extraction_schema=MentionExtractionResult,
    prompt_id="mention_extraction",
    validation_tool="validate_mentions",
    repair_prompt_id="mention_repair",
    max_retries=3,
    model_override="qwen2.5:7b-instruct",
    prompt_dir="k8s/congress-data/prompts",
)
```

### Pipeline Composition

Chain stages where each stage's accepted output feeds the next:

```python
from catalyst_exgraph.pipeline import build_pipeline

pipeline = build_pipeline(
    stages=[ner_config, spo_config],
    clients={"ner": gliner_client, "spo": llm_client},
    mcp_client=mcp_client,
)
result = await pipeline.ainvoke({"raw_text": text, "stages": {}})
```

### Ensemble Extraction

Run N models and merge by consensus:

```python
from catalyst_exgraph.ensemble import ConsensusVoter, EnsembleExtractNode

voter = ConsensusVoter(strategy="majority", threshold=0.5, kind="ner")
accepted = voter.vote({
    "gliner": gliner_mentions,
    "universalner": universalner_mentions,
    "mistral": mistral_mentions,
})
# Each accepted item has consensus_score and contributing_models
```

### ExtractionResource (Dagster)

```python
# In code location __init__.py:
resources = {
    "extraction": ExtractionResource(
        ner_model="gliner",
        spo_model="mistral:latest",
        prompt_dir="k8s/media-ingest/prompts",
        max_concurrency=5,
    ),
}

# In assets:
@asset
def media_mentions(chunks, extraction: ExtractionResource):
    return extraction.extract_mentions(chunks, code_location="media_ingest").mentions

@asset
def media_assertions(chunks, media_mentions, extraction: ExtractionResource):
    return extraction.extract_assertions(
        chunks, accepted_mentions=media_mentions, code_location="media_ingest"
    ).assertions
```

## Strangler Fig Migration

Enable the new pipeline with an environment variable:

```bash
# Use old pipeline (default)
EXGRAPH_ENABLED=false

# Use new pipeline
EXGRAPH_ENABLED=true
```

The flag is read by `dagster_io/extraction.py`. Both pipelines produce identical output formats — `extract_validated()` and the benchmark suite work with either.

### Benchmarking

```bash
# Benchmark with v1 (current):
PYTHONPATH=. pytest tests/test_extraction_benchmark.py::TestRunAll -v -s

# Benchmark with v2 (exgraph):
EXGRAPH_ENABLED=true PYTHONPATH=. pytest tests/test_extraction_benchmark.py::TestRunAll -v -s --regen
```

## Module Reference

| Module | Purpose |
|--------|---------|
| `config.py` | StageConfig, PipelineConfig, preset helpers |
| `state.py` | ExGraphState, ExGraphStatus, StageStateDict |
| `protocol.py` | ExtractionClient protocol, StageResult, ExtractionResult |
| `stage.py` | `build_stage_graph()` — generic extract→validate→repair loop |
| `pipeline.py` | `build_pipeline()` — chains stages, `pipeline_result_to_legacy()` |
| `resource.py` | ExtractionResource — Dagster ConfigurableResource |
| `dispatch.py` | Strangler fig dispatcher (EXGRAPH_ENABLED) |
| `ensemble.py` | EnsembleExtractNode, ConsensusVoter |
| `nodes/extract.py` | Generic ExtractNode |
| `nodes/validate.py` | Generic ValidateNode (MCP contract validation) |
| `nodes/repair.py` | Generic RepairNode (LLM repair with span hints) |
| `nodes/spans.py` | Shared span computation utilities |

## Testing

```bash
# Run all tests
pytest libs/catalyst-exgraph/tests/ -v

# 121 tests covering:
# - Config validation (14)
# - State transitions (6)
# - Protocol conformance (6)
# - Span computation (15)
# - Stage graph paths (12)
# - Pipeline composition (12)
# - Resource behavior (17+)
# - Dispatch/strangler fig (8)
# - Ensemble consensus (21)
```
