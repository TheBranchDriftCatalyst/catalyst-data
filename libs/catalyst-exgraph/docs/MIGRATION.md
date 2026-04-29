# Migration Guide: catalyst-langgraph-aio → catalyst-exgraph

## Overview

`catalyst-exgraph` replaces the hardcoded NER→SPO extraction graph with a generic, composable pipeline. Migration uses a strangler fig pattern — both old and new coexist, toggled by `EXGRAPH_ENABLED`.

## Step 1: Enable the Feature Flag (Zero Code Changes)

Set `EXGRAPH_ENABLED=true` in your environment. The existing `extract_validated()` function in `dagster_io/extraction.py` will use the new exgraph pipeline instead of the old graph.

```yaml
# k8s deployment manifest
env:
  - name: EXGRAPH_ENABLED
    value: "true"
```

This is safe because:
- The v2 pipeline produces the same output format as v1
- The same MCP contract validators are used
- All existing asset code continues to work unchanged

## Step 2: Adopt ExtractionResource (Per Code Location)

Replace `extract_validated()` calls with `ExtractionResource` methods.

### media-ingest

**Before:**
```python
from dagster_io.extraction import extract_validated

@asset
def media_mentions(media_chunks: list[TextChunk]) -> list[Mention]:
    all_mentions, _ = extract_validated(media_chunks, code_location="media_ingest")
    return all_mentions

@asset
def media_assertions(media_chunks: list[TextChunk]) -> list[Assertion]:
    _, all_assertions = extract_validated(media_chunks, code_location="media_ingest")
    return all_assertions
```

**After:**
```python
from catalyst_exgraph import ExtractionResource

@asset
def media_mentions(media_chunks: list[TextChunk], extraction: ExtractionResource) -> list[Mention]:
    return extraction.extract_mentions(media_chunks, code_location="media_ingest").mentions

@asset
def media_assertions(
    media_chunks: list[TextChunk],
    media_mentions: list[Mention],  # Dagster asset dependency
    extraction: ExtractionResource,
) -> list[Assertion]:
    return extraction.extract_assertions(
        media_chunks, accepted_mentions=media_mentions, code_location="media_ingest"
    ).assertions
```

**Resource config** (`packages/media-ingest/src/media_ingest/__init__.py`):
```python
resources = {
    "extraction": ExtractionResource(
        ner_model="gliner",
        spo_model="mistral:latest",
        prompt_dir="k8s/media-ingest/prompts",
    ),
}
```

### congress-data

Same pattern. Key difference: congress uses partitioned assets.

**Resource config** (`packages/congress-data/src/congress_data/__init__.py`):
```python
resources = {
    "extraction": ExtractionResource(
        ner_model="mistral:latest",
        spo_model="mistral:latest",
        prompt_dir="k8s/congress-data/prompts",
    ),
}
```

### open-leaks

Same pattern.

**Resource config** (`packages/open-leaks/src/open_leaks/__init__.py`):
```python
resources = {
    "extraction": ExtractionResource(
        ner_model="mistral:latest",
        spo_model="mistral:latest",
        prompt_dir="k8s/open-leaks/prompts",
    ),
}
```

## Step 3: Validate

Run the benchmark suite with both pipelines and compare:

```bash
# v1 baseline
PYTHONPATH=. pytest tests/test_extraction_benchmark.py::TestRunAll -v -s

# v2 comparison
EXGRAPH_ENABLED=true PYTHONPATH=. pytest tests/test_extraction_benchmark.py::TestRunAll -v -s --regen
```

Compare mention/assertion counts, F1 scores, and timing.

## Step 4: Remove Old Code (After Validation)

Once v2 is validated in production:

1. Remove `EXGRAPH_ENABLED` flag check in `dagster_io/extraction.py`
2. Remove `extract_validated()` function
3. Remove `catalyst-langgraph-aio` dependency from code locations
4. Remove old graph code

## Key Differences

| Aspect | v1 (langgraph-aio) | v2 (exgraph) |
|--------|-------------------|--------------|
| Graph topology | Hardcoded NER→SPO | Composable stages |
| Model selection | Global `LLM_MODEL` env var | Per-stage via ExtractionResource |
| Prompts | `PROMPT_REGISTRY_DIR` env var | `prompt_dir` on StageConfig/Resource |
| NER→SPO chaining | Internal (invisible) | Dagster asset dependency (visible, cacheable) |
| Ensemble | Not supported | N models → consensus voting |
| Encoder models | `max_retries=0` hack | Native `StageConfig.max_retries=0` |
| Wasted computation | Both NER+SPO always run | `extract_mentions()` / `extract_assertions()` separate |
