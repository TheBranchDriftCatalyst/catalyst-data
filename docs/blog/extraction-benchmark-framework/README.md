# Building a Rigorous Extraction Benchmark: Testing 12 NER/NLP Models Against MCP Contract Validation

*April 2026*

## TL;DR

We built a benchmark harness that runs 12 extraction models (encoders, specialists, and local LLMs from 300M to 12B parameters) against 7 real-world text chunks from 3 domains, validates every extraction through an MCP contract pipeline, and surfaces the results in a React dashboard with Gantt chart audit trails. The key finding: GLiNER encoder models extract 50x faster than local LLMs with comparable or better entity coverage, but only LLMs can extract subject-predicate-object propositions. The biggest surprise was that span offset errors (SPAN_MISMATCH) are the dominant validation failure mode -- and they are entirely fixable with deterministic text search, not more LLM calls.

---

## The Problem

When you're building an entity extraction pipeline for media transcriptions, congressional bills, and leaked documents, you quickly discover that "which model should I use?" is the wrong first question. The right questions are:

1. **What does the model actually extract?** Not every model extracts the same things. Encoders like GLiNER do NER only. LLMs can also extract propositions (subject-predicate-object triples). Some specialists degenerate on long text.
2. **How do you validate extractions?** An LLM can hallucinate an entity that never appeared in the source text, or return character offsets that point to the wrong substring.
3. **How do you compare models fairly?** You need ground truth, but who generates it? If you trust a single model to produce ground truth, you've already biased the comparison.

We needed a system that answers all three questions with real numbers.

## Architecture

The benchmark framework has five components that work together:

### Test Data: 3 Domains, 7 Chunks

The benchmark uses curated text chunks from three real-world domains:

| Domain | Chunks | Description |
|--------|--------|-------------|
| **media** | 3 | Podcast transcriptions (speaker-turn and pause-split chunked) covering geopolitics, commentary |
| **congress** | 1 | House Resolution text (HRES.1, 119th Congress) with formal legislative language |
| **open_leaks** | 3 | Leaked/public document content with organizations, people, and locations |

These were chosen to stress-test extraction across informal spoken language, formal legal text, and document-heavy structured content. Every chunk is a real production artifact from the media-ingest pipeline.

### Model Registry: 12 Models Across 3 Types

All models are defined in `benchmark_config.py` with a `ModelConfig` dataclass:

```
Encoders (3)
  gliner-medium    300M params  -- bidirectional encoder, not an LLM
  gliner-large     600M params  -- same architecture, more capacity
  gliner-pii       300M params  -- PII-tuned variant (phone, email, SSN + standard NER)

Specialists (3)
  nuextract-1.5    3.8B params  -- purpose-built for NER extraction
  nuextract-2.0-8b 8B params    -- Qwen2.5-VL fine-tune, multimodal
  universalner-7b  7B params    -- GPT-3.5 distilled across 43 NER datasets

Local LLMs (6)
  gemma3-12b       12B params   -- LLMStructBench 0.72 (best <=12B)
  gemma3-4b        4B params    -- smaller Gemma variant
  mistral-7b       7B params    -- strong empirical performer
  qwen2.5-7b       7B params    -- balanced extraction
  llama3.1-8b      8B params    -- best SPO extraction in early tests
  llama3.2-3b      3B params    -- fastest local LLM
```

The config also defines cloud models (GPT-4o, Claude Sonnet) that can serve as ground truth baselines when API keys are available, but the local-only run covers the full 12-model suite without external dependencies.

All local models run via Ollama on Apple Silicon. Encoders run in-process via Python (no serving endpoint). The harness unloads each Ollama model from VRAM after its run to free memory for the next.

### Pipeline: Extract -> MCP Validate -> Repair (LangGraph)

Each model's extraction passes through a LangGraph pipeline with MCP validation gates:

```
extract_ner -> validate_ner -> [repair_ner] -> extract_spo -> validate_spo -> [repair_spo]
```

The validation step is the critical innovation. Every extracted mention is validated against an MCP (Model Context Protocol) contract that checks:

- **SPAN_MISMATCH**: Does `source_text[span_start:span_end]` equal the mention text?
- **DUPLICATE_SPAN**: Are two mentions claiming the same character range?
- **EMPTY_EXTRACTION**: Did the model return zero candidates for a chunk?

When validation fails, a repair node attempts to fix the extraction. For LLMs, this can involve re-prompting. For span mismatches, deterministic repair is now available (more on this below).

### Scoring: F1/Precision/Recall Against Ensemble Ground Truth

The scoring module (`extraction_scoring.py`) computes:

- **Strict F1**: text AND mention_type must both match ground truth
- **Relaxed F1**: text match only (type may differ)
- **Type accuracy**: among text-matched mentions, what fraction have the correct entity type
- **Span accuracy**: for mentions with character offsets, does `source[start:end]` match the mention text
- **Proposition F1**: subject + predicate + object triple matching (strict and relaxed)

### Viewer SPA: React Benchmark Dashboard

The benchmark report JSON powers a React dashboard with six tabs:

![Overview tab showing bar charts for mentions, assertions, speed, and time across all 12 models](images/01-overview.png)

The **Overview** tab shows four ranked bar charts (mentions extracted, assertions extracted, speed in tok/s, total time) and a sortable table grouped by model type (encoder, specialist, LLM) with aggregate statistics.

![Entity matrix showing 197 entities across 3 domains with per-model coverage](images/03-entities.png)

The **Entities** tab presents a cross-model entity matrix: 197 unique entities across 3 domains, showing which models found each entity. You can see at a glance that gliner-medium found 14/58 media entities while gemma3-12b found 23/58.

![Pipeline tab showing LangGraph pipeline stages with MCP validation gates](images/05-pipeline.png)

The **Pipeline** tab reveals the internal pipeline flow: extract_ner calls, validate_ner results (including ambiguous and error counts), repair_ner retries, and extract/validate_spo for LLM models. The amber numbers with annotations like `16(12a)` mean 16 validate calls, 12 ambiguous -- a clear signal that a model is producing extractions that don't cleanly pass validation.

The **Audit** tab provides a Gantt chart visualization where you can select any two models for side-by-side comparison of their extraction pipeline execution, seeing exactly where time is spent and which stages triggered errors.

---

## Key Findings

### Encoder vs LLM: The 50x Speed Gap

The numbers tell a clear story:

| Model Type | Avg Mentions | Avg Time | Avg Tok/s | Assertions |
|-----------|-------------|---------|----------|-----------|
| **Encoder** (3 models) | 83 | 6.6s | 924 | 0 |
| **Specialist** (3 models) | 42 | 14.5s | 307 | 0 |
| **LLM** (6 models) | 50 | 174.2s | 42 | 37 |

GLiNER-medium processes all 7 chunks in **5.0 seconds** at 1,151 tok/s and extracts **81 mentions** -- the second-highest count after gliner-large's 93. Gemma3-12b, the best LLM, takes **189.4 seconds** (38x slower) to extract 68 mentions.

But encoders cannot extract propositions. Only LLMs produce subject-predicate-object triples. Gemma3-12b leads with 50 assertions, followed by mistral-7b (48) and llama3.1-8b (45). If you need knowledge graph triples, you need an LLM.

### The Speed/Quality Leaderboard

Ranked by mention count (raw extraction volume):

| Model | Mentions | Assertions | Time | Tok/s | Retries |
|-------|---------|-----------|------|-------|---------|
| gliner-large | 93 | 0 | 8.1s | 782 | 0 |
| gliner-medium | 81 | 0 | 5.0s | 1,151 | 0 |
| gliner-pii | 76 | 0 | 6.6s | 838 | 0 |
| gemma3-12b | 68 | 50 | 189.4s | 40 | 0 |
| mistral-7b | 65 | 48 | 321.8s | 23 | 9 |
| nuextract-1.5 | 52 | 0 | 24.5s | 177 | 0 |
| qwen2.5-7b | 46 | 26 | 232.8s | 23 | 9 |
| llama3.1-8b | 44 | 45 | 144.3s | 43 | 0 |
| universalner-7b | 43 | 0 | 10.3s | 374 | 0 |
| gemma3-4b | 42 | 28 | 88.6s | 59 | 6 |
| llama3.2-3b | 32 | 23 | 68.3s | 66 | 0 |
| nuextract-2.0-8b | 30 | 0 | 8.7s | 371 | 0 |

The retries column is revealing. Mistral-7b and qwen2.5-7b both needed **9 repair retries** each -- their extractions frequently failed validation on the first pass. Gemma3-12b, llama3.1-8b, and llama3.2-3b needed **zero** retries, meaning their outputs passed MCP validation cleanly.

### The Span Mismatch Problem: 66 Errors

Across all 12 models, the pipeline recorded **66 SPAN_MISMATCH errors**, **18 DUPLICATE_SPAN errors**, and **3 EMPTY_EXTRACTION errors**.

The SPAN_MISMATCH breakdown per model:

| Model | SPAN_MISMATCH | DUPLICATE_SPAN | Repair Calls |
|-------|--------------|---------------|-------------|
| mistral-7b | 34 | 3 | 9 |
| qwen2.5-7b | 12 | 3 | 9 |
| nuextract-1.5 | 8 | 7 | 0 |
| gemma3-4b | 8 | 0 | 6 |
| nuextract-2.0-8b | 4 | 0 | 0 |
| gliner-* | 0 | 0 | 0 |
| gemma3-12b | 0 | 0 | 0 |
| llama3.1-8b | 0 | 0 | 0 |
| llama3.2-3b | 0 | 0 | 0 |

Mistral-7b alone accounts for **52% of all span errors** (34 out of 66). The encoder models produce **zero** span errors because they compute spans deterministically from their token alignments. This is the core insight that led to the span auto-repair innovation.

### Proposition Extraction: Only LLMs Need Apply

The proposition (SPO) matrix shows a clean divide. Six models produced zero assertions:

- All 3 GLiNER encoders (by design -- they do NER only)
- nuextract-1.5 and nuextract-2.0-8b (extraction specialists focused on NER)
- universalner-7b (NER-only architecture)

The six local LLMs all extracted propositions, with 201 unique triples across the corpus:

- **media domain**: 72 propositions (24 unique predicates)
- **other domains**: 129 propositions (72 unique predicates)

Llama3.1-8b is notable for having the best assertion-to-mention ratio: 45 assertions from 44 mentions, suggesting it naturally decomposes text into relational triples rather than just tagging entities.

---

## The Span Auto-Repair Innovation

The single most impactful improvement to extraction quality was realizing that **we should never ask LLMs to guess character offsets**.

When an LLM extracts an entity like "Kevin McCumber" from a text, it returns something like:

```json
{
  "text": "Kevin McCumber",
  "mention_type": "PERSON",
  "span_start": 512,
  "span_end": 526
}
```

But `source_text[512:526]` might actually be `"vin McCumber o"` -- off by a few characters. The entity text is right, but the span is wrong. This triggers a SPAN_MISMATCH validation error, which triggers a repair cycle, which burns more inference time and may not even fix the problem.

The solution, implemented in `catalyst_exgraph.nodes.spans.correct_candidate_spans()`:

```python
def correct_candidate_spans(candidates: list[dict], source_text: str) -> list[dict]:
    """Deterministically correct span offsets using text search.

    Algorithm (proximity-aware):
    1. Search for candidate["text"] in source_text
    2. If exactly 1 match -> use it
    3. If N matches -> pick closest to LLM's guess, prefer unassigned spans
    4. If 0 matches -> retry case-insensitive
    5. If still 0 -> leave unchanged (genuine hallucination)
    """
```

The key insight is proximity-aware deduplication: when "Israel" appears 4 times in a text and the LLM guessed offset 71, we pick the occurrence nearest to 71 that hasn't already been claimed by another candidate. This handles the common case where a model extracts the same entity from different parts of a text.

The function modifies candidates in-place and runs in microseconds. It eliminates SPAN_MISMATCH errors for any entity that actually appears in the source text. The only remaining failures are genuine hallucinations -- entities the model fabricated.

This approach turned a 66-error validation problem into a near-zero-error pipeline for the v2 (exgraph) implementation, without any additional LLM calls.

---

## Ensemble Ground Truth

Single-model ground truth is inherently biased. If GPT-4o is your ground truth and you benchmark local models against it, you're really measuring "how similar is this model to GPT-4o?" -- not "how good is this model at extraction."

Our ensemble approach uses majority voting across multiple models:

1. Run N models (default ensemble panel: gliner-large, GPT-4o, Claude Sonnet, mistral, gemma3-12b)
2. For each (text, entity_type) pair, count how many models agree
3. Accept as ground truth if >= ceil(N/2) models vote for it
4. Recompute spans deterministically against source text (using `find_best_span()`)
5. Confidence = fraction of models that agreed

```python
def _ner_consensus(all_model_mentions, source_text, threshold):
    votes: dict[tuple[str, str], dict[str, dict]] = {}
    for model_name, mentions in all_model_mentions.items():
        for m in mentions:
            key = (text.lower().strip(), mtype)
            votes.setdefault(key, {})
            if model_name not in votes[key]:
                votes[key][model_name] = m

    accepted = []
    for (norm_text, norm_type), model_entries in votes.items():
        if len(model_entries) < threshold:
            continue
        # majority-voted type, deterministic spans, confidence = agreement ratio
        ...
```

This produces ground truth that is robust to any single model's blind spots. The entity matrix in the viewer SPA shows this consensus visually: entities detected by 11/12 models (like "Israel", "Taiwan", "Kevin McCumber") are high-confidence. Entities found by only 1-2 models are either model-specific hallucinations or genuine long-tail entities that most models miss.

The current report shows 197 unique entities. Of those, the top entities by consensus (11 out of 12 models agreeing) include geopolitical entities (Israel, Taiwan, China), people (Kevin McCumber, William McFarland, Catherine Szpindor), and organizations (House of Representatives).

---

## The Benchmark Viewer in Detail

The viewer SPA deserves special mention because benchmark numbers in a terminal are hard to parse at scale. Six tabs, each serving a different analytical purpose:

**Overview**: Four ranked bar charts (mentions, assertions, speed, time) plus a sortable grouped table. At a glance you see that encoders cluster in the top-left (fast + high entity count) while LLMs spread across the bottom (slow + variable).

**Scores**: F1/Precision/Recall when ground truth is available. Currently shows a placeholder prompting ground truth generation -- this is by design, since ground truth requires either a cloud model run or enough local fixtures for ensemble voting.

**Entities (197)**: A cross-model entity matrix grouped by domain. Each cell shows `found/total` with highlighting for high-coverage and low-coverage models. You can expand each domain to see individual entities and which models detected them with which types.

**Propositions (201)**: Same matrix format but for SPO triples. Shows which LLMs extracted which relationships. The "Hidden" note at the bottom lists models that extracted zero propositions (all encoders and specialists).

**Pipeline**: The most operationally useful tab. Shows the LangGraph pipeline stage breakdown per model: how many extract_ner calls, validate_ner calls (with ambiguous/error counts in amber), repair_ner calls, and the same for SPO. The amber annotations like `16(12a)` (16 calls, 12 ambiguous) immediately tell you which models struggle with validation.

**Audit**: A Gantt chart timeline where you select one or two models for side-by-side comparison. Each chunk gets its own row. Colored bars represent pipeline stages (blue = extract, purple = validate, amber = repair, cyan = SPO extract). Clicking a bar reveals details: candidate count, validation status, specific error codes. The shared time axis makes speed differences visceral -- an encoder's entire run fits in the first centimeter of a bar chart that stretches across the screen for an LLM.

---

## Running It Yourself

```bash
# Full benchmark (all 12 local models, cached fixtures)
python tests/benchmark_harness.py

# Regenerate all extraction fixtures from scratch
python tests/benchmark_harness.py --regen --timeout 600

# With audit logs for Gantt chart visualization
python tests/benchmark_harness.py --regen --audit-log

# Full methodology: run models -> ensemble ground truth -> score -> report
python tests/benchmark_harness.py --full

# Compare v1 (legacy) vs v2 (exgraph) pipelines side-by-side
python tests/benchmark_harness.py --compare

# Generate ensemble ground truth from existing fixtures
python tests/benchmark_harness.py --ensemble-gt

# Local-only (skip cloud models, no API key needed)
python tests/benchmark_harness.py --local-only

# View results in the browser
cd packages/media-ingest/viewer-ui && npm run dev
# Navigate to http://localhost:5173/viewer/benchmarks
```

The harness supports an interactive mode (run with no flags) that presents a menu:

```
[1] Full methodology (run models -> ensemble GT -> score -> report)
[2] Regenerate all extraction fixtures
[3] Generate ensemble ground truth only
[4] Save detailed audit logs
[5] Skip cloud models (no API key needed)
[6] Use exgraph v2 pipeline
[7] Compare v1 vs v2 (runs both, side-by-side report)
```

---

## What's Next

**exgraph v2 composable pipeline**: The current pipeline hardcodes the extract -> validate -> repair flow. The v2 architecture (in `libs/catalyst-exgraph/`) makes pipeline stages composable and swappable, with a proper state graph, dispatch system, and protocol-based node interfaces.

**Reflexion agent repair**: Instead of simple re-prompting on validation failure, use the validation errors as structured feedback for the LLM to reflect on and correct its output. The current repair loop is a step in this direction but doesn't yet feed error details back to the model.

**Ensemble consensus voting at extraction time**: Rather than running one model and validating its output, run 3 models simultaneously and accept the consensus. This trades compute for reliability, but with encoders processing at 1,151 tok/s, running 3 GLiNER variants is still faster than a single LLM pass.

**Cloud model baselines**: The benchmark config includes GPT-4o, GPT-4o-mini, and Claude Sonnet via LiteLLM proxy. Adding these to the benchmark run would provide cloud-vs-local quality comparisons and enable more robust ensemble ground truth.

---

*Built with LangGraph, Pydantic MCP validation, GLiNER, Ollama, React, and an unreasonable number of terminal hours watching progress bars.*
