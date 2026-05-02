# LLM Extraction Model Research (April 2026)

## Reference
- **LLMStructBench**: [Benchmarking LLMs for Structured Data Extraction](https://arxiv.org/html/2602.14743v1)
- **Ticket**: CD-lfe (Implement NuExtract + Gemma 4 model split)
- **Previous ticket**: CD-cgk (Quality issues with qwen3:30b-a3b)

<!-- NOTE: Thjis codument is pretty outdated -->

## Problem Statement

Current extraction pipeline uses `qwen3:30b-a3b` (MoE) for both NER and assertion extraction. Quality issues observed:
- Wrong entity types (GPE for "House of Representatives", "119th CONGRESS")
- Self-referential assertions: (Bill) --[was introduced]--> (Bill)
- Reversed SPO direction: (Sponsor) --[was sponsored by]--> (Bill)
- Noise assertions, no dedup
- OpenAI gpt-4o-mini produced noticeably better structured output

## Model Selection

### For NER/Mention Extraction: NuExtract-2.0-8B

| Property | Value |
|----------|-------|
| Source | [numind/NuExtract-2.0-8B](https://huggingface.co/numind/NuExtract-2.0-8B) |
| Parameters | 8B |
| Architecture | Fine-tuned for structured extraction |
| Ollama | `ollama pull nuextract` (3.8B v1) or manual for 2.0 |
| Deployment | Mac M3 via Ollama (runs alongside nomic-embed-text) |
| VRAM | ~5GB (FP16) |

**Why**: Purpose-built for extraction. Uses template-based approach where you provide a JSON schema template and it fills values. Achieves highest F1 on NER benchmarks among local models. Not a general chat model — purely extractive.

### For Assertion/SPO Extraction: Gemma 4 27B

| Property | Value |
|----------|-------|
| Source | google/gemma-4-27b (HuggingFace) |
| Parameters | 27B |
| Architecture | Dense transformer, instruction-tuned |
| Deployment | RunPod serverless (A40 48GB) |
| VRAM | ~20GB (Q4_K_M) |

**Why**: Best open-source model for structured reasoning and instruction following (April 2026). Google prioritized mathematical logic and structured output in Gemma 4. Better tool-calling compliance than Qwen3.

### For Embeddings: nomic-embed-text (unchanged)

768-dim vectors, runs on Mac CPU. No change needed.

## Prompt Strategy: P-Strategy (from LLMStructBench)

The paper evaluated 5 prompting strategies across 22 models. **P-Strategy** won for 11/22 models:

### Key Principles

1. **Include JSON schema explicitly in system prompt** — don't rely on API-level format constraints
2. **Provide 2-3 concrete input→output examples** (few-shot)
3. **Keep schemas flat** — nested depth >4 degrades quality significantly
4. **Value accuracy is the bottleneck**, not schema compliance
5. **Choosing the right prompting strategy is more important than model size**

### Error Categories (ranked by severity)

1. **Wrong Values (WV)** — dominant bottleneck across all model sizes. "Value fidelity, rather than structural compliance, is the main bottleneck."
2. **Missing Keys (MK)** — largely resolved at 8B+ parameters
3. **Missing Values (MV)** — less severe, correlates with missing keys

### Anti-patterns

- Using API format parameter alone (PJ strategy) — forces structural compliance but increases semantic errors
- Highly nested schemas (depth >4) — "did not result in even medium quality generated examples"
- Generic prompts without examples — models guess entity types and SPO directions

## Post-Processing Filters

Applied after LLM extraction, before persistence:

1. **Dedup mentions by span** — same (start, end, type) = keep first
2. **Filter self-referential assertions** — subject == object
3. **Normalize predicate direction** — canonical form: (Actor) --[verb]--> (Target)
4. **Confidence threshold** — drop assertions with confidence < 0.7
5. **Entity type validation** — reject known bad mappings (GPE for organizations)

## Architecture

```
Text Chunk
    │
    ├──→ NuExtract-2.0-8B (Mac/Ollama) ──→ Mentions (NER)
    │        template-based extraction
    │
    └──→ Gemma 4 27B (RunPod) ──→ Assertions (SPO)
             P-Strategy prompt with examples
    │
    └──→ nomic-embed-text (Mac/Ollama) ──→ Embeddings
             unchanged
```

## LiteLLM Model Names

```yaml
ollama-mac/nuextract          # NER extraction (Mac M3)
runpod/gemma4:27b             # SPO assertions (RunPod A40)
ollama-mac/nomic-embed-text   # Embeddings (Mac M3, unchanged)
```

## Environment Variables

```
LLM_MENTION_MODEL=ollama-mac/nuextract
LLM_ASSERTION_MODEL=runpod/gemma4:27b
EMBEDDING_MODEL=ollama-mac/nomic-embed-text  # unchanged
```
