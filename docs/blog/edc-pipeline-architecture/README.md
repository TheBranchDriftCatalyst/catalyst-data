# Entity-Driven Chunking: Building an Open Information Extraction Pipeline

*May 2026*

> **Architecture post.** For model-level benchmarking details see
> [extraction-benchmark-framework](../extraction-benchmark-framework/README.md).
> For the multi-video corpus workflow see
> [multi-video-benchmark](../multi-video-benchmark/README.md).

---

## TL;DR

We are building a pipeline that turns heterogeneous unstructured sources — WikiLeaks diplomatic cables, congressional bills, podcast transcripts — into structured knowledge: named-entity mentions and subject-predicate-object assertions ready for downstream knowledge graphs. The pipeline runs through a Bronze → Silver → Gold medallion architecture on Dagster, with extraction handled by a composable LangGraph library called ExGraph.

The most important thing we shipped in May 2026 was the v3 entity-anchored topology rewrite: instead of running NER and SPO extraction independently on each raw chunk, we now run NER once across the entire document, cluster the resulting entities by proximity and embedding similarity, pack the clusters into model-sized evidence windows, and fan out SPO extraction per window. The motivation: before v3, the dominant failure mode was not "wrong entity type" or "hallucinated span" — it was "the model is looking at text that has no extractable claims in it."

Hot on the heels of v3, **v4 (shipped May 2026, commit `9c3c854`)** replaces the single-NER pass with a parallel encoder ensemble that votes a consensus before anything reaches the cluster step. Every consensus decision is audit-logged; the State Inspector surfaces per-encoder NER cards and a dedicated consensus card per document. The SPO prompt now receives per-entity vote tallies and confidence scores so the LLM can weight propositions according to how strongly the encoder panel agreed on each entity.

---

## 1. Why Open Information Extraction

A vector database is not enough. Embedding a WikiLeaks cable and indexing it for semantic retrieval lets you answer "find cables related to Iran" — but it does not let you answer "what did the State Department assert about Ahmadinejad's relationship to the IRGC, and how did that change between 2007 and 2010?" That question requires structured assertions: a subject, a predicate, an object, all traceable back to a specific passage in a specific document.

The formal output we are after is two-layer:

- **Mentions** — named-entity spans with type (PERSON, ORG, GPE, EVENT, …) and character offsets. A mention is a pointer: it says "this span of text refers to this kind of thing."
- **Assertions** — subject-predicate-object triples where the subject and object are linked to mention IDs. An assertion is a claim: "entity A `[predicate]` entity B, as stated in [document, chunk, span]."

Together they form a lightweight knowledge graph that can answer relational queries, power entity timelines, and serve as training signal for preference optimization (DPO) of extraction models.

The data is deliberately heterogeneous because that is where the hard problems live. Congressional bills use formal legislative language with defined terms; WikiLeaks cables use State Department prose with abbreviations and signal-to-noise problems baked in by format; podcast transcripts are diarized speech with filler, overlap, and diarization artifacts. Any extraction pipeline that works on one domain can be made to work on another — but it usually does so by overfitting to domain-specific signals that do not transfer. The benchmark exists to catch that.

---

## 2. EDC Architecture: Bronze → Silver → Gold -> Platinum

The pipeline follows a **medallion architecture** with three layers, each implemented as a set of Dagster Software-Defined Assets (SDAs) inside a per-domain code location.

```
Bronze (raw ingest)
  ↓
Silver (chunked + cleaned)
  ↓
Gold (mentions + assertions + embeddings)
  ↓
Platinum (downstream graph storage, not covered here)
```

### Per-Domain Code Locations

Four code locations, each with its own Python package, Docker image, and Dagster definitions:

| Code Location | Package | Domain |
|---|---|---|
| `media_ingest` | `packages/media-ingest` | YouTube/podcast video → transcript → diarized chunks |
| `congress_data` | `packages/congress-data` | Congressional bill text → structured clauses |
| `open_leaks` | `packages/open-leaks` | WikiLeaks, ICIJ entities, Epstein documents |
| `knowledge_graph` | (downstream) | Aggregates gold-layer output into graph storage |

The split is not arbitrary. Each domain has different bronze ingestion (YouTube URLs vs Congress API vs WikiLeaks bulk download), different silver chunking strategy (speaker-turn segmentation vs clause-boundary splitting vs paragraph chunking), and different prompt context needed for accurate extraction. A single monolithic pipeline would either need a wall of `if domain == ...` branches or would sacrifice domain-appropriate behavior. Per-domain code locations give each domain a clean separation while sharing the extraction library (`catalyst-exgraph`) and IO managers (`dagster-io`) as shared libraries.

### Silver Layer: Chunking

The `ChunkingResource` in `libs/dagster-io/src/dagster_io/chunking.py` is the canonical chunker. It exposes `chunk_speaker_segments(...)` for media-ingest and `chunk_document(...)` for text-only domains. Both methods produce `TextChunk` objects with a stable `chunk_id`, a `document_id`, and a `metadata` dict that records the chunking strategy, target character size, overlap, and — critically — the `chunk_char_offset` (the chunk's start position in absolute document coordinates).

The `chunk_char_offset` is not decorative. Every downstream GT entry is anchored to it (see section 6).

### Gold Layer: Mentions and Assertions

Each code location defines sibling assets — `media_mentions` / `media_assertions`, `congress_mentions` / `congress_assertions`, etc. — that call `extract_validated()` from `dagster_io.extraction` against the silver-layer chunks. The function is shared. Domain-specific behavior comes entirely from the prompt files loaded via `PROMPT_REGISTRY_DIR` (at `k8s/<domain>/prompts/`) and the model configured in `ExtractionResource`.

### IO Manager Unification

Production assets write to MinIO via `MinioIOManager`. In development and tests, `LocalJsonIOManager` writes to identical medallion paths under `.test-output/<domain>/<layer>/`. The IO backend is selected at startup via `select_io_managers(DAGSTER_IO_BACKEND)`. This means `task bench:chunks:regen` and `task bench` exercise the exact same asset code as production — only the storage backend changes.

### The Bench Loop

Layered on top of the production pipeline is a benchmark harness (`tests/benchmark_harness.py`) that:

1. Runs each of 15 registered models (300M-parameter encoders through cloud LLMs) against a shared chunk corpus.
2. Generates consensus ground truth via majority voting across an ensemble panel.
3. Scores every model against GT with leave-one-out to eliminate tautological bias.
4. Outputs a `benchmark-report.json` consumed by the React benchmark viewer.

The harness does not implement its own extraction. It calls `extract_validated()` via the same `dagster_io.extraction` path production uses. Benchmark results measure real pipeline behavior, not a test approximation.

---

## 3. ExGraph: Composable Extraction

The extraction library (`libs/catalyst-exgraph/`) provides the building blocks. Its core abstraction is the **stage graph**: every extraction stage — NER, SPO, or any future variant — compiles into the same `extract → validate → repair` subgraph parameterized by a `StageConfig`.

```
StageConfig
    │
    ▼
build_stage_graph()
    │
    ▼
┌───────────────────────────────┐
│  extract → validate → repair  │
│  (parameterized by config)    │
└───────────────────────────────┘
```

`StageConfig` specifies: the extraction schema (Pydantic model), the prompt ID, the MCP validation tool name, the repair prompt ID, max retries, and an optional model override. Presets exist for NER and SPO:

```python
ner = ner_stage_config(model="gliner", max_retries=0)
spo = spo_stage_config(model="mistral:latest", max_retries=3)
```

The validate node runs every extracted candidate through an MCP (Model Context Protocol) contract check:

- `SPAN_MISMATCH` — does `source_text[span_start:span_end]` equal the mention text?
- `DUPLICATE_SPAN` — two mentions claiming the same character range?
- `EMPTY_EXTRACTION` — did the model return zero candidates?

When validation fails, the repair node either applies deterministic span correction (`correct_candidate_spans()` in `nodes/spans.py` — a proximity-aware text search that resolves off-by-N character errors without re-prompting) or, for structural failures, re-prompts the LLM with the validation errors as structured feedback.

The result: span alignment errors, which accounted for 66 out of 87 total validation failures in the original 12-model benchmark run, are now fixed deterministically in microseconds rather than burning an LLM retry.

Stages compose into pipelines:

```python
pipeline = build_pipeline(
    stages=[ner_config, spo_config],
    clients={"ner": gliner_client, "spo": llm_client},
    mcp_client=mcp_client,
)
```

Each stage's accepted output feeds into the next stage's `upstream_context`. The NER stage's accepted mentions are available to the SPO stage as `upstream_context["accepted_mentions"]` — the SPO prompt receives the entity list along with the text, which keeps the model's attention on turning those specific entities into relational triples rather than re-discovering entities from scratch.

Ensemble extraction is available via `ConsensusVoter` — run N models, accept an entity if `>= ceil(N/2)` agree on `(normalized_text, type)`. This is how ground truth is generated: the ensemble panel votes, and the winner set becomes the GT that is then refined with human annotation.

---

## 4. The Chunking Problem That Drove v3

The original pipeline was straightforward: for each chunk, run NER, then run SPO. Simple, easy to reason about, easy to test. It was also the source of a systematic quality problem that did not show up in aggregate metrics.

Consider two real examples from the benchmark corpus.

**WikiLeaks cable 1985USUN02134, chunk 0.** This chunk has 13 entity mentions but zero propositions. The extraction is not wrong — the model correctly identified the entities. The problem is what the chunk contains: it is the cable masthead. "UNCLASSIFIED", the routing header, the date stamp, classification labels, the origin office, the destination offices. Thirteen labels, no claims. The model ran a full NER pass, validated cleanly, then ran a full SPO pass and produced nothing, because there is literally nothing to claim in a header.

**Joe Rogan Experience 2284, chunk 0.** 119 characters. "Good morning everybody. Happy Thursday." Two mentions: `SPEAKER_07` (a pyannote diarization label that leaked into the text) and "Happy Thursday" (tagged as an EVENT or MISC depending on the model). Neither is a useful entity. The chunk is a greeting that happens to appear at position 0 of the transcript where chunker boundary cuts fall most often.

These are not edge cases. They are examples of a pervasive structural problem: character-boundary chunking cuts where it is convenient for the chunking algorithm, not where the semantic content is. Headers, greetings, boilerplate, speaker labels, and transitional phrases all get their own chunks, and each one runs the full extraction stack.

The practical impact: in a naive chunk-parallel pipeline, SPO extraction on a 119-character greeting costs the same pipeline infrastructure as SPO extraction on a dense 1,500-character paragraph of substantive content. The model is slower to process good text because it is spending cycles on content that cannot produce claims.

The v2 pipeline partially addressed span errors. But it did not address the deeper problem: the chunker does not know where the entities are, and the extractor does not know where the good text is.

---

## 5. The v4 NER Ensemble + Consensus Topology

v3 shipped the entity-anchored topology (NER-once-per-doc → cluster → pack → SPO fan-out). v4 replaces the "NER once" step with a parallel encoder ensemble that votes a consensus, then feeds that consensus into the unchanged cluster → pack → SPO chain. The motivation is recall and fairness.

**Recall.** `gliner-pii` is the only default encoder that reliably finds phone numbers, SSNs, and email addresses. `universalner-7b` has domain coverage from 43 NER datasets. `nuextract-2.0-8b` surfaces structured entities that general-purpose GLiNER models under-count. No single encoder sees everything; a quorum-filtered union of five sees substantially more.

**Bench fairness.** Under v3, the "benchmark NER" step ran one reference encoder. Every model's output was compared against that reference, which made the bench measure "similarity to the reference encoder" rather than "how well does this encoder find entities independently." Under v4, each encoder runs against raw text and produces its own independent fixture. Bench scores measure Phase 1 quality in isolation.

### v4 Topology

```
                    ┌─────────────────────────────────────┐
                    │  Phase 1 — NER ensemble (parallel)   │
                    │  gliner-medium ┐                     │
doc text  ────────► │  gliner-large  ├─► per-model         │
                    │  gliner-pii    ├─► mentions          │
                    │  nuextract-2   ├─►                   │
                    │  universalner  ┘                     │
                    └─────────────┬───────────────────────┘
                                  ▼
                    ┌─────────────────────────────────────┐
                    │  Phase 2 — Consensus + scoring       │
                    │  Cluster by (text, canonical_type,   │
                    │  span overlap). Per cluster:         │
                    │   - source_models: [m1, m2, m3]      │
                    │   - vote_count: 3/5                  │
                    │   - mean_confidence: 0.79            │
                    │   - span: from highest-conf model    │
                    │   - type: majority vote on canonical │
                    │  Apply quorum threshold (default     │
                    │  ceil(N/2); per-type override for    │
                    │  PII at K=1).                        │
                    └─────────────┬───────────────────────┘
                                  ▼
                    ┌─────────────────────────────────────┐
                    │  Phase 3 — Cluster + pack            │
                    │  (proximity + Qwen3 embed-merge,     │
                    │   on consensus mentions, unchanged   │
                    │   from v3 cluster_entities/pack)     │
                    └─────────────┬───────────────────────┘
                                  ▼
                    ┌─────────────────────────────────────┐
                    │  Phase 4 — SPO per evidence window   │
                    │  Prompt includes consensus metadata: │
                    │   "Entities (NER votes / mean conf): │
                    │    - Reagan  [PERSON, 5/5, 0.94]     │
                    │    - Putin   [PERSON, 4/5, 0.87]     │
                    │    - Crimea  [LOC,    3/5, 0.62]"    │
                    │  SPO returns assertions linked to    │
                    │  canonical entity ids; LLM can       │
                    │  weight proposition confidence by    │
                    │  vote_count / mean_conf.             │
                    └─────────────────────────────────────┘
```

The new nodes live at:
- `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/ner_ensemble.py` — `NerEnsembleNode`
- `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/consensus.py` — `ConsensusNode`
- `libs/catalyst-exgraph/src/catalyst_exgraph/consensus_taxonomy.py` — per-encoder type canonicalization map

### Consensus Voting in Detail

A single mention's journey through Phase 2, using "Reagan" as the example:

Five encoders each independently run NER on the document text. Three (`gliner-large`, `gliner-medium`, `universalner-7b`) return a mention with surface text "Reagan" and type `PERSON`. One (`gliner-pii`) returns "Reagan" with type `GPE`. One (`nuextract-2.0-8b`) returns "Reagan" with type `PERSON`.

`ConsensusNode._canonicalize()` lowercases the text and maps each encoder's type vocabulary through `consensus_taxonomy.TYPE_CANONICAL[encoder]` to get canonical types. `gliner-pii`'s `GPE` stays `GPE`; the other four map to `PERSON`.

`ConsensusNode._cluster()` groups these five mentions using union-find: same `canonical_text` ("reagan") and span overlap ≥ 50% (all five encoders found approximately the same span). One cluster, five members.

`ConsensusNode._resolve_cluster()` computes:
- `type_votes = {"PERSON": 4, "GPE": 1}` — majority wins: `canonical_type = "PERSON"`
- `span_start`, `span_end` — from `gliner-large` (highest confidence in the cluster)
- `vote_count = 5`, `n_encoders = 5`
- `mean_confidence = 0.94`
- `source_models = ["gliner-medium", "gliner-large", "gliner-pii", "nuextract-2.0-8b", "universalner-7b"]`

Quorum check: default `K = ceil(5/2) = 3`. `vote_count = 5 ≥ 3` → accepted.

The resulting `ConsensusMention` (defined in `libs/catalyst-exgraph/src/catalyst_exgraph/state.py`):

```python
{
    "mention_id": "a3f1c9d2e8b4",       # md5(canonical_text|type|span_start)[:12]
    "text": "reagan",
    "canonical_type": "PERSON",
    "span_start": 4102,
    "span_end": 4108,
    "span_provenance": "gliner-large",
    "source_models": ["gliner-medium", "gliner-large", "gliner-pii",
                      "nuextract-2.0-8b", "universalner-7b"],
    "vote_count": 5,
    "n_encoders": 5,
    "mean_confidence": 0.94,
    "type_votes": {"PERSON": 4, "GPE": 1},
    "raw_mentions": [...]                 # per-encoder source mentions preserved
}
```

A `mention_decision` audit event is emitted with `chunk_id = "{doc_id}:_consensus"` so the State Inspector's consensus card surfaces this vote table for every accepted mention. Mentions that don't reach quorum emit a `mention_rejected` event with `reason = "below_quorum"` and the vote count that fell short — no silent drops.

### Why a Consensus Instead of One NER

The recall ceiling argument: each encoder in the default ensemble (`gliner-medium`, `gliner-large`, `gliner-pii`, `nuextract-2.0-8b`, `universalner-7b`) has different strengths.

`gliner-pii` catches phone numbers, SSNs, email addresses, and postal addresses that the other four models never produce because they were not trained on PII entity types. PII-category mentions use a K=1 quorum override — they pass if `gliner-pii` finds them alone, because K=1 is the correct threshold for a category with asymmetric coverage by design.

`universalner-7b` was distilled from GPT-3.5 across 43 NER datasets covering biomedical, legal, and multilingual domains. It surfaces entity types (`PRODUCT`, `ARTWORK`, `LAW`) that general-purpose GLiNER variants undercount on congressional bill text.

`nuextract-2.0-8b` is a Qwen2.5 fine-tune purpose-built for structured extraction; it surfaces compound named entities and numerical references that slip past encoder-only models.

The quorum-filtered union of five consistently produces higher recall than any individual encoder, at the cost of running five NER passes in parallel — which, because the passes are `asyncio.gather`-concurrent with 60-second individual timeouts, adds only the latency of the slowest encoder to the pipeline, not the sum.

### How SPO Uses the Votes

The SPO node in `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/extract.py` calls `_format_entity_provenance(accepted_mentions)` to build the entity block inserted before the source text in the SPO prompt:

```
Entities (with NER agreement):
  - reagan                         [PERSON, 5/5 votes, mean_conf 0.94]
  - putin                          [PERSON, 4/5 votes, mean_conf 0.87]
  - crimea                         [LOCATION, 3/5 votes, mean_conf 0.62]
  - speaker_07                     [PERSON, 1/5 votes, mean_conf 0.41]
```

The SPO system prompt (`k8s/shared/prompts/proposition_extraction.prompt`) teaches the LLM what this means:

> High vote\_count + high mean\_conf → reliable entity, safe to relate.
> Low vote\_count (e.g. 1/5) or low mean\_conf (< 0.5) → weakly supported.
> Prefer omitting relations involving these unless the source text strongly justifies them. When in doubt, omit a proposition rather than fabricate one.

The function gracefully degrades: for legacy bare-mention dicts that carry no consensus metadata (single-NER production callers that still use `build_ner_pipeline`), it falls back to the `[TYPE]` format. Mixed lists — some consensus, some legacy — are handled without errors.

**Numbers from the upcoming v4 bench will populate this section once Phase 4 validation has run.** The expectation is higher SPO recall on weakly-supported entities (because the LLM now has a signal for which entities are reliable vs. uncertain) and lower hallucination rate on sub-quorum entities.

---

## 5a. The v3 Entity-Anchored Topology (foundation for v4)

The v3 rewrite inverts the dependency. Instead of asking "what entities and claims live in this chunk?", we ask "where do the entities live in this document, and what text do we need to include to give each cluster of entities enough context to produce accurate claims?"

Under v3, the topology is a single-NER pass followed by cluster → pack → SPO. Under v4, the NER step expands into a parallel ensemble + consensus (Phases 1–2 above), and the cluster → pack → SPO chain (Phases 3–4) is unchanged. The description below covers the common phases.

The topology is implemented in `libs/dagster-io/src/dagster_io/extraction.py` with stage nodes in `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/`:

```
[v3]  extract_ner → validate_ner → repair_ner ─┐
[v4]  NerEnsembleNode → ConsensusNode          ─┘
                │
                ▼
     cluster_entities  (ClusterEntitiesNode)
                │
                ▼
     pack_evidence     (PackEvidenceNode)
                │
         ┌──────┘
         │  outer driver fans out per evidence window
         ▼
extract_spo → validate_spo → repair_spo
                │
                ▼
     persist_artifacts
```

Each step has a specific job.

### NER Input to the Cluster Step

Under v3: `_group_chunks_into_docs()` in `extraction.py` concatenates all of a document's chunks into a single `_Doc` object. NER runs on the full document text — every mention of "Reagan" across 40 chunks is found in a single pass. The NER stage is backed by the same `extract_ner → validate_ner → repair_ner` subgraph from ExGraph. `raw_text` is the full document text, not a chunk slice.

Under v4: `NerEnsembleNode` runs the same full-doc text through N encoders in parallel via `asyncio.gather`. `ConsensusNode` receives `state["per_encoder_mentions"]` (a dict keyed by encoder name) and emits `state["consensus_mentions"]` — a list of `ConsensusMention` objects that the cluster step reads instead of the single-NER accepted mentions. The `build_ensemble_pipeline()` function in `libs/catalyst-exgraph/src/catalyst_exgraph/pipeline.py` wires these two new nodes as the NER head; the rest of the pipeline is unchanged.

### ClusterEntitiesNode: Proximity + Embedding Merge

`ClusterEntitiesNode` (`nodes/cluster.py`) groups the NER output into clusters of nearby, related entities. Two passes:

**Proximity pass.** Mentions are sorted by `doc_char_start`. A sliding window merges any pair of consecutive mentions whose gap is at most `proximity_radius` characters (default 200). This is linear time and catches the most common case: multiple entities mentioned in the same sentence or paragraph.

**Embedding merge.** For each proximity cluster, a context snippet (±200 chars around the cluster's bounding box) is embedded via `EmbeddingResource` (Qwen3-8B; see section 7). Pairs of clusters with cosine similarity ≥ 0.75 AND at least one shared entity by surface form are merged. The shared-entity guard is important: it prevents merging two topically similar but entity-disjoint passages that happen to have high embedding similarity. The resulting merged clusters are regrouped via union-find.

Why both passes? Proximity alone catches "Israel and Lebanon" in the same sentence but misses "Reagan in paragraph 1 and Reagan in paragraph 25." Embedding alone may merge two paragraphs that are topically related but contain different entities — the shared-surface-form guard prevents false merges.

### PackEvidenceNode: Per-Model Window Sizing

`PackEvidenceNode` (`nodes/pack.py`) converts each entity cluster into an evidence window: `text[cluster_start - context_padding : cluster_end + context_padding]`, clipped to document bounds. If the resulting window exceeds the target model's context limit, it splits on sentence boundaries.

The model's context limit comes from `MODEL_WINDOWS` in `pack.py` — a mapping from model name to conservative context token count (80% of advertised, to leave room for system prompt):

```python
MODEL_WINDOWS = {
    "gliner-medium": 320,     # GLiNER's hard sub-word limit
    "mistral": 8192,
    "gemma3-12b": 24576,
    "gpt-4o": 65536,
    ...
}
```

GLiNER's 384-token context hard limit is not a suggestion — it silently truncates input. Without per-model window sizing, feeding GLiNER a 2,000-character evidence window silently discards ~75% of the text. With `MODEL_WINDOWS`, the packer sizes each window to what the model can actually attend to.

### SPO Fan-Out per Evidence Window

After packing, `_process_doc()` in `extraction.py` fans out SPO extraction: one `spo_pipeline.ainvoke()` call per evidence window, with the window's text and the cluster's mention list pre-populated into `upstream_context["accepted_mentions"]`. This gives each SPO invocation the tightest possible text slice containing the entities most likely to produce a claim.

The fan-out is done by the outer Python driver, not inside a LangGraph. This was a deliberate choice: keeping the fan-out in `extraction.py` rather than encoding it as a LangGraph branch makes the audit trail clean (each SPO call is its own LangGraph invocation with its own event sequence), makes the code straightforward to parallelize per-window in the future, and keeps the StateInspector's per-evidence-window timeline straightforward to render.

### Net Effect

Before v3: one NER call and one SPO call per chunk. A 40-chunk document produced 40 NER calls and (for encoder-only models) 0 or 40 SPO calls.

After v3: one NER call per document, N SPO calls where N is the number of entity clusters with evidence windows. For a document where most chunks are noise (headers, greetings, boilerplate), N is substantially smaller than the chunk count. The target is approximately a 3x reduction in SPO calls on realistic corpus content, with higher quality because each SPO context is coherent text centered on the entities being asked about.

After v4: the single NER pass is replaced by 5 parallel NER passes (default ensemble) followed by one consensus pass. The wall-clock cost of Phase 1+2 is approximately the latency of the slowest encoder (not the sum, because they run concurrently). The cluster → pack → SPO chain is identical to v3. The SPO prompt now carries vote tallies that let the LLM skip or downweight propositions involving entities only one encoder found.

<!-- TODO: figure showing before/after call count per document, annotated with the wikileaks and JRE examples -->

---

## 6. Span-Anchored Ground Truth

The v3 topology changes where entities live relative to chunks. Every time the chunker is tuned — chunk size adjusted, pause threshold changed, boundary strategy swapped — existing ground truth entries keyed by `chunk_id` become orphans. The entity spans are chunk-relative, so changing what a chunk contains changes whether a GT entry matches any real chunk.

Phase 0 of the v3 epic (`tests/shared/gt_translation.py`, `docs/SEED.md`) solved this by anchoring GT to document-absolute character positions.

### Old Format

```json
{
  "chunk_id": "joe-rogan-2284:chunk-7",
  "mentions": [
    {"text": "Alex Jones", "mention_type": "PERSON", "span_start": 18, "span_end": 28}
  ]
}
```

`span_start: 18` means 18 characters into chunk 7. If the chunker changes and chunk 7 no longer starts in the same place, this is useless.

### New Format (v2, shipped)

```json
{
  "doc_id": "joe-rogan-2284",
  "doc_char_start": 18432,
  "doc_char_end": 19280,
  "mentions": [
    {
      "text": "Alex Jones",
      "mention_type": "PERSON",
      "doc_char_start": 18450,
      "doc_char_end": 18460
    }
  ]
}
```

The join key is `(doc_id, IntervalTree[doc_char_start, doc_char_end))`. At scoring time, for each model-output chunk:

1. Look up `gt_index[chunk.document_id]` — an `IntervalTree`.
2. Query `[chunk_char_offset, chunk_char_offset + len(chunk.text))` against the tree.
3. For each overlapping GT entry, subtract `chunk_char_offset` from each mention's doc-frame span to get chunk-relative coordinates, then score.

The consequence: future chunker changes are free. Re-score without re-annotating. The v3 topology change itself — full-doc NER instead of per-chunk NER — does not invalidate any GT entry because GT is anchored to document positions, not to what any particular chunker emitted.

The migration script (`scripts/migrate_gt_to_doc_anchored.py`) converts existing GT files in place, backing up originals to S3. Translation relies on `chunk_char_offset` in chunk metadata, which `ChunkingResource` records for all chunkers that track it. The `chunk_to_doc()` and `doc_to_chunk()` helpers in `tests/shared/gt_translation.py` handle the arithmetic and raise `ValueError` for un-mappable chunks (produced by older chunkers that did not record offset).

---

## 7. Qwen3-Embedding-8B: Why This Model for Clustering

The embedding merge step in `ClusterEntitiesNode` uses `EmbeddingResource` with `provider=local`, which resolves to Qwen3-Embedding-8B via a local ONNX runtime.

**Why Qwen3-8B over BGE-large or stella-400M?** At time of selection, Qwen3-Embedding-8B ranked at the top of the MTEB (Massive Text Embedding Benchmark) English leaderboard for models in its size class. More practically: it is instruction-tunable (the `instruct` flag routes queries through a task prefix), it supports matryoshka representation learning, and it runs locally without API calls.

**Why matryoshka truncation to 2048 dimensions?** The native output dimension is 4096. Matryoshka training means the first 2048 dimensions of the 4096-dim vector preserve essentially the same ranking quality as the full vector — the model was trained to have this property explicitly. Storing 2048 vs 4096 floats per vector is a 2x storage saving with negligible quality cost at the similarity thresholds we use (≥ 0.75). The `EmbeddingCache` in `libs/dagster-io/src/dagster_io/embedding_cache.py` uses `dim=2048` as its key parameter.

**Why local over API?** Three reasons. First, embeddings are deterministic: the same text produces the same vector across runs, which means the S3-backed `EmbeddingCache` (sharded parquet at `s3://dagster/silver/embedding_cache/`) makes all reruns after the first pass essentially free — the vectors are already there. Second, privacy: cable content and diplomatic correspondence should not leave the homelab. Third, the hardware: M5 Max with 128 GB unified memory means 8B-parameter models fit comfortably without paging. The embedding throughput on this hardware is fast enough that the clustering step is not a bottleneck in the pipeline.

**Instruction-aware encoding.** The model supports separate query and document encoding modes via instruction prefixes. In `ClusterEntitiesNode`, all texts are encoded as documents (no query prefix). The infrastructure for instruction-aware search — where a user query gets the query prefix and corpus texts get the document prefix — is wired in `EmbeddingResource` but is not yet used in production. It is the enabling layer for a future semantic search path over the gold layer.

---

## 8. Provenance as a Design Principle

Every extraction artifact carries a full `Provenance` object linking it back to the text it came from. This is not logging sugar — it is the mechanism that makes the extraction pipeline debuggable.

The `Provenance` fields threaded through from silver to gold:

| Field | What it records |
|---|---|
| `source_document_id` | Which document |
| `chunk_id` | Which chunk within the document |
| `span_start`, `span_end` | Character offsets into the chunk text |
| `extraction_model` | Which model produced this extraction (e.g., `mistral:latest`) |
| `code_location` | Which Dagster code location ran it (e.g., `media_ingest`) |
| `speaker_label` | Speaker from diarization (media only) |
| `temporal_start_ms`, `temporal_end_ms` | Time range from audio segmentation (media only) |

Every `chunk_loaded` event in the unified DuckDB-backed audit log carries a `chunk_metadata` block with chunking strategy, size, overlap, char offset, and content hash. This means the StateInspector can display not just what was extracted from a chunk but why the chunk has the shape it has — which chunker produced it, with what parameters.

The audit event stream from ExGraph emits per-stage events tagged with `(model, doc_id, chunk_id, evidence_window_id)`. In the v3 topology this extends to the evidence window level: each SPO invocation emits events tagged with the evidence window ID, so the StateInspector's timeline can group events by window and show the full sequence — NER, cluster, pack, SPO — as a coherent trail for one entity cluster in one document.

In the v4 topology, the event stream gains three new chunk_id patterns:

- `{doc_id}:_ner_{encoder_name}` — per-encoder NER events. One card per (doc, encoder) in the State Inspector rail.
- `{doc_id}:_consensus` — consensus events. The consensus card surfaces a vote table: one row per accepted mention with `text · canonical_type · vote_count/N · mean_conf · source_models`, plus a collapsed section of rejected mentions showing the below-quorum count and reason.
- `{doc_id}:{window_id}` — per-SPO-window events (unchanged from v3).

Every consensus decision is audit-logged. No mention is silently dropped: accepted mentions emit `mention_decision` events; below-quorum mentions emit `mention_rejected` events with the quorum that was not met. This satisfies the observability requirement locked in the v4 epic: the State Inspector consensus card can show exactly why a given mention was kept or discarded.

The intent is simple: if a model produces a wrong assertion — say it attributes a position to the wrong person, or invents a relationship — the provenance chain should let you trace it to the exact text passage that produced it, the exact chunk boundary that isolated it, the exact evidence window that the SPO model saw. Without this chain you can measure that something went wrong; with it you can understand why.

`Assertion` objects link back to `Mention` IDs via `subject_mention_id` and `object_mention_id`, completed at the end of `extract_validated()` via a `(chunk_id, normalized_text) → mention_id` index. This closes the provenance chain: document → chunk → span → mention → assertion.

---

## 9. Debugging Surfaces: StateInspector and AuditViewer

Two interfaces in the React viewer SPA (`packages/media-ingest/viewer-ui/`) make the pipeline's internals observable during development and benchmarking.

**StateInspector** (`/viewer/state-inspector`). A three-pane interface: left rail selects model → domain → document, center pane shows a scrollable chunk timeline with per-chunk extraction status, right pane drills into a selected chunk and shows the extraction events in sequence — `chunk_loaded`, `extract_ner`, `validate_ner`, per-window SPO events, `chunk_extracted`. The events stream live from a unified `events.jsonl` file written by the harness and exposed via SSE from the FastAPI viewer backend. In the v3 topology, the right pane will surface the evidence window breakdown: which entities were clustered together, what text the SPO model received, what it extracted.

**Benchmark Viewer** (`/viewer/benchmarks`). Six tabs: Overview (ranked bar charts by mentions, assertions, speed), Scores (F1/precision/recall per model), Entities (cross-model entity matrix), Propositions (SPO triple matrix), Pipeline (per-model stage breakdown with MCP validation stats), Audit (Gantt chart of pipeline execution, per-model, per-chunk). The audit Gantt is particularly useful for spotting models that spend disproportionate time in repair cycles vs. models that pass validation cleanly on the first pass.

Both surfaces read from the same event stream, so a single benchmark run produces all the data both UIs need.

---

## 10. What Is Deliberately Out of Scope

**Re-ranking and RAG retrieval.** The gold-layer mentions and assertions are designed to feed downstream consumers. Retrieval-augmented generation over the knowledge graph is a downstream problem — this pipeline produces the structured data; how it is queried is not our responsibility here.

**SFT/DPO training data pipeline.** The GT sampler (`scripts/benchmark/sample_gt_candidates.py`, `docs/SEED.md`) produces a curated set of 200+ diversity-sampled chunks for human annotation. The training data export (GT + per-model extractions → JSONL for DPO) is a tracked downstream task (CD-foy3). It depends on the GT being at scale (CD-erc3) and the GT editor workflow being complete.

**Multi-tenant deployment patterns.** The pipeline runs in a Kubernetes cluster (ArgoCD syncs `k8s/` to the `catalyst-data` namespace). Container builds, namespace layout, ArgoCD image-updater config — this is all platform engineering layered on top of the pipeline design. It exists and it works, but it is not the design we are writing about here.

---

## What's In Flight

**v3 topology** (sections 5a-8) — `extract_ner → cluster_entities → pack_evidence → SPO fan-out` — shipped. See `libs/dagster-io/src/dagster_io/extraction.py` and `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/cluster.py`, `nodes/pack.py`.

**v4 ensemble extraction** (section 5) — `NerEnsembleNode → ConsensusNode → cluster → pack → SPO` — shipped as of commit `9c3c854`. Closes phases CD-7h9m, CD-94ow, CD-3w3n, CD-z6xe, CD-mjww of epic CD-y4u0. See `nodes/ner_ensemble.py`, `nodes/consensus.py`, `consensus_taxonomy.py`.

**Phase F of v4** (`CD-euro`) — State Inspector consensus card UI. The event stream already carries all consensus data; the `ConsensusDetail.tsx` component that renders the vote breakdown table in the right pane is the remaining frontend work.

**Phase 3** (`CD-80ic`) — moving `MODEL_WINDOWS` from `nodes/pack.py` into `dagster_io.chunking` so the same token-budget table governs both silver-layer chunk sizing and evidence window sizing. Currently `pack.py` has a `# TODO(CD-80ic)` comment marking this migration.

**v4 bench validation** — the first end-to-end benchmark run comparing per-encoder fixtures vs. the ensemble fixture vs. per-SPO-model fixtures has not run yet. Numbers from that run will be added here when available. Until then, the v4 sections in this post describe the code as shipped, not validated against scoring results.

---

## Running the Pipeline

```bash
# Install all packages
pip install -e libs/dagster-io -e libs/catalyst-exgraph \
            -e packages/congress-data -e packages/media-ingest -e packages/open-leaks

# Run a local Dagster dev UI for one code location
cd packages/media-ingest && dagster dev -m media_ingest

# Benchmark: full methodology (run models → GT → score → report)
task bench

# Benchmark: run specific SPO model against the consensus output
PYTHONPATH=. python tests/benchmark_harness.py --spo-models mistral-7b

# Benchmark: NER-only pass — run the full encoder ensemble, emit per-encoder + ensemble fixtures
PYTHONPATH=. python tests/benchmark_harness.py --ensemble gliner-medium,gliner-large,gliner-pii --ensemble-only

# Benchmark: override consensus quorum (require all encoders to agree)
PYTHONPATH=. python tests/benchmark_harness.py --ensemble-quorum 5

# Benchmark: reuse a previous run's cached consensus for a new SPO model
PYTHONPATH=. python tests/benchmark_harness.py --spo-only --run-id 2026-05-01-120000 --spo-models gemma3-12b

# Benchmark: v3 fairness path — each encoder runs standalone NER+SPO (no consensus)
PYTHONPATH=. python tests/benchmark_harness.py --no-consensus

# StateInspector + Benchmark Viewer
cd packages/media-ingest/viewer-ui && npm run dev
# Navigate to http://localhost:5173/viewer/state-inspector
# Navigate to http://localhost:5173/viewer/benchmarks
```

---

## Key Files

| File | Purpose |
|---|---|
| `libs/dagster-io/src/dagster_io/extraction.py` | Outer driver: doc grouping → NER/ensemble → cluster → pack → SPO fan-out |
| `libs/catalyst-exgraph/src/catalyst_exgraph/pipeline.py` | `build_ner_pipeline` (v3/production), `build_ensemble_pipeline` (v4 bench) |
| `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/ner_ensemble.py` | `NerEnsembleNode` — parallel encoder invocation via asyncio.gather |
| `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/consensus.py` | `ConsensusNode` — cluster + vote + quorum filter + audit events |
| `libs/catalyst-exgraph/src/catalyst_exgraph/consensus_taxonomy.py` | Per-encoder type → canonical type map; `PII_TYPES` K=1 override set |
| `libs/catalyst-exgraph/src/catalyst_exgraph/state.py` | `ConsensusMention` TypedDict; `ExGraphState` with `per_encoder_mentions` + `consensus_mentions` |
| `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/extract.py` | `ExtractNode` + `_format_entity_provenance()` — SPO entity block builder |
| `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/cluster.py` | `ClusterEntitiesNode` — hybrid proximity+embedding clustering |
| `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/pack.py` | `PackEvidenceNode` — per-model window sizing + `MODEL_WINDOWS` |
| `libs/catalyst-exgraph/src/catalyst_exgraph/stage.py` | `build_stage_graph()` — generic extract→validate→repair loop |
| `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/spans.py` | `correct_candidate_spans()` — deterministic span repair |
| `k8s/shared/prompts/proposition_extraction.prompt` | SPO system prompt with NER ensemble provenance instructions |
| `libs/dagster-io/src/dagster_io/cluster_cache.py` | `CachedNerResult` — extended with `per_encoder_mentions`, `evidence_windows`, `rejected_mentions` |
| `libs/dagster-io/src/dagster_io/chunking.py` | `ChunkingResource` — silver-layer chunker |
| `libs/dagster-io/src/dagster_io/embedding_cache.py` | S3-backed embedding cache (sharded parquet) |
| `tests/shared/gt_translation.py` | `chunk_to_doc`, `doc_to_chunk`, `IntervalTree` GT scorer |
| `docs/SEED.md` | GT format v2 (doc-anchored), sampler design, scoring join math |
| `BENCHMARK.md` | Full benchmark reference: models, CLI flags, scoring methodology |

---

*the Owl is coming...*
