# Feature Proposal: Reflexion-Enhanced Repair for catalyst-exgraph

## Background

The Reflexion pattern (Shinn et al., [arXiv 2303.11366](https://arxiv.org/abs/2303.11366)) introduces a three-phase loop:

1. **Act** — agent performs extraction
2. **Evaluate** — MCP contract validation grades the output
3. **Reflect** — separate LLM call analyzes *why* errors occurred, produces diagnostic memo
4. **Re-Act** — agent retries with reflection memo as episodic memory

The key insight: reflection separates "diagnose the problem" from "fix the problem" — the LLM reasons about *patterns* in its failures before attempting repair.

## Current vs Proposed Repair Flow

**Current:**
```
extract → validate → [repair → validate]* → END
                      └── errors + candidates + spans → LLM → fixed candidates
```

**Proposed:**
```
extract → validate → [reflect → repair → validate]* → END
                      │                └── memo + errors + candidates → LLM → fixed
                      └── errors + candidates → LLM → diagnostic memo
```

| Aspect | Current RepairNode | Reflexion-Enhanced |
|--------|-------------------|-------------------|
| Error analysis | Implicit (LLM infers from raw errors) | Explicit reflection produces diagnostic memo |
| Strategy | None — just "fix these errors" | "I confused GPE with LOC because..." |
| Memory across retries | None — each retry independent | Reflection memos accumulate |
| LLM calls per repair cycle | 1 | 2 (reflect + repair) |

## When Reflexion Adds Value

**High value (reflect):**
- `INVALID_TYPE` — type confusion (ORG vs GPE, LOC vs FACILITY). LLM needs to reason about *why* it chose wrong.
- `INVALID_REFERENCE` — SPO referencing non-existent mentions. Needs inventory awareness.
- `MISSING_REQUIRED_FIELD` — systematic schema misunderstanding.
- Recall problems — consistently missing entity types.

**Low value (skip reflection, just repair):**
- `SPAN_MISMATCH` — mechanical, solved by `compute_correct_spans()` hints
- `CONFIDENCE_OUT_OF_RANGE` — trivial clamping
- `DUPLICATE_SPAN` — mechanical dedup

## Implementation Sketch

### StageConfig additions
```python
reflexion_enabled: bool = False          # opt-in, default off
reflection_model: str | None = None      # cheaper model for reflect step
reflexion_error_threshold: list[str] | None = None  # only reflect on these codes
```

### ReflectNode
```python
class ReflectNode:
    """Analyzes validation errors and produces a diagnostic memo."""
    async def __call__(self, state):
        errors = stage["validation"]["errors"]
        # Classify: high-value vs mechanical
        if only_mechanical(errors):
            return {"memo": "Skip reflection — mechanical errors only"}
        # Full reflection via LLM
        memo = await client.complete(reflection_prompt + errors + candidates)
        stage["reflection_memos"].append(memo)
        return updated_state
```

### Stage graph wiring
```python
if config.reflexion_enabled and config.max_retries > 0:
    graph.add_node("reflect", ReflectNode(...))
    graph.add_edge("reflect", "repair")
    # validate routes to "reflect" instead of "repair"
```

## Cost/Latency Analysis

| Metric | Current | Reflexion | Net |
|--------|---------|-----------|-----|
| Calls per repair cycle | 1 | 2 | +100% per cycle |
| Total calls (3 retries) | 4 | 7 | +75% worst case |
| But: if reflexion fixes in 1 retry | 4 calls | 3 calls | -25% |

**Optimization**: use cheaper model for reflection (text reasoning, no structured output needed). `llama3.1:8b` for reflect, `gpt-4o` for repair.

## Interaction with Encoder Models

None needed. Encoder models use `max_retries=0` → repair loop never reached → `ReflectNode` never instantiated.

## Recommendation

**Implement behind `reflexion_enabled=False` flag.** The architecture is clean — adds one node to the existing stage graph pattern. Error classification provides natural short-circuit for mechanical errors. Accumulated reflection memos provide audit trail value.

Defer cheaper-model optimization to second iteration. Benchmark first with same model for both steps.
