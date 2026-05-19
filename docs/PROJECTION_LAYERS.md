# Projection Layers — LangGraph ↔ Dagster SDA ↔ Medallion

## Why this matters

One source of truth for "where does this op's output land". Without this table, contributors have to grep across both repos (`catalyst-llm/packages/catalyst-exgraph` + `catalyst-data/packages/*`) to figure out the LangGraph node → Dagster SDA → S3 prefix mapping. This file is the canonical reference.

`catalyst-llm` owns the **projection operations** (LangGraph nodes that project one representation into the next) + the **benchmark tools** that grade them. `catalyst-data` is the **consumer side** — it runs each projection op as a Dagster step (in a k8s pod via `dagster-k8s`), captures the step's output as a **software-defined asset** (SDA), and the SDA's persistence path is the medallion layer.

The shared shape registry is `catalyst-contracts-core`; the Dagster-side entry point is `catalyst_exgraph.resource.ExtractionResource`. Each row in the table below is one IOManager round-trip. The contracts-core unification gives both the catalyst-llm bench harness and the catalyst-data Dagster ingest the same Pydantic shapes; the S3 IOManager schema is the wire format. **`Assertion` from contracts-core is what gets serialized to Parquet** — same bytes whether bench reads it or Dagster reads it.

## The mapping table

| LangGraph node (catalyst-exgraph) | Dagster SDA (catalyst-data) | Medallion layer | Wire shape (contracts-core) | S3 prefix |
|---|---|---|---|---|
| (Congress.gov API client) | `bill_documents` | bronze | `congress_data.entities.BillDetail` | `bronze/congress/bill_documents/{partition}/` |
| `ChunkNode` (BillChunker) | `congress_chunks` | silver | `dagster_io.models.TextChunk` | `silver/congress/congress_chunks/{partition}/` |
| (`ChunkingResource` + `attach_seed` via `SemanticChunkingSeed`) | `{domain}_chunks` | silver | `TextChunk` + `SemanticChunkingSeed` metadata | `silver/{domain}/{domain}_chunks/{partition}/` |
| `NerEnsembleNode` (4 voters) | (transient — internal to gold step) | — | `dict[str, list[MentionCandidate]]` | not persisted |
| `ConsensusNode` | `congress_mentions` | gold | `contracts_core.Mention` (consensus-aware) | `gold/congress/congress_mentions/{partition}/` |
| `ClusterEntitiesNode` + `PackEvidenceNode` | (transient) | — | `EntityCluster` + `EvidenceWindow` | not persisted |
| `AmrParseNode` | (transient OR `congress_amr_parses` if we want training data) | — / gold-aux | `AmrSentenceParse` (penman strings) | `gold/congress/amr_parses/{partition}/` |
| `AmrToAssertionNode` | `congress_assertions` | gold | `contracts_core.Assertion` (AMR-aware) | `gold/congress/congress_assertions/{partition}/` |
| (`ConcordanceEngine` — within-source) | `congress_entity_candidates` | gold | `dagster_io.models.EntityCandidate` | `gold/congress/entity_candidates/{partition}/` |
| (`CrossSourceAligner` — across sources) | `canonical_entities`, `alignment_edges` | platinum | `dagster_io.models.CanonicalEntity` + `AlignmentEdge` | `platinum/canonical_entities/` (unpartitioned) |
| (Neo4j writer in `GraphDBResource`) | `assertion_graph` | platinum | `Statement` nodes + `:participates_in` edges | Neo4j primary; n10s exports Turtle-star on demand |

### Semantic chunking row — what it adds

The `SemanticChunkingSeed` row sits between the raw chunker output and NER. It uses the `embedding_seed` resource convention — see `libs/dagster-io/src/dagster_io/semantic_seed.py` for the existing implementation. The seed embeds each chunk with a deterministic model so downstream concordance and clustering use a stable, cached representation instead of re-computing per-asset.

## Pod orchestration

Existing — kept. `dagster-k8s` runs each step as a `k8s_job_op`. The launcher reads the SDA's resource requirements (CPU/GPU for the AMR parser, RAM for clustering) from the asset definition. `catalyst-llm`'s `ExtractionResource` reads `prompt_dir` + `label_pack_id` from the resource config so each code location (`congress-data`, `media-ingest`, `open-leaks`) gets its domain pack without code branching.

## Two consumer sides

- **catalyst-llm bench harness** (`scripts/benchmark/`, `dagster_io.bench`) — reads gold-layer assertion shards directly off S3 for offline scoring, ground-truth comparison, SFT/DPO dataset construction. Doesn't need Dagster context.
- **catalyst-data Dagster ingest** — the live materialization path. SDA dependencies drive incremental rebuilds when chunkers change or label packs bump.

Both sides read/write the same Pydantic shapes from `catalyst-contracts-core`. The S3 IOManager Parquet schema is the wire format.

## Label pack selection

`ExtractionResource` picks the AMR label pack from the `label_pack_id` resource config (NOT from `code_location`, though `extract_validated()` still has a `code_location → label_pack_id` lookup for backwards compat). Per-code-location wiring:

| Code location | Label pack | Prompt dir (post K refactor) |
|---|---|---|
| `congress_data` | `congress` | `k8s/base/congress-data/prompts` |
| `media_ingest` | `media` | `k8s/base/media-ingest/prompts` |
| `open_leaks` | `generic` (no domain pack yet) | `k8s/base/open-leaks/prompts` |

## Where to extend

- **New domain pack**: drop a new label pack in `catalyst-llm/packages/catalyst-exgraph/label_packs/<domain>/`, then point the consumer's `ExtractionResource(label_pack_id="<domain>")` at it.
- **New SDA in the projection chain**: add a row above, declare it in the domain's `definitions.py`, persist via the standard `select_io_managers()` output of `dagster_io`.
- **Cross-source resolution**: lands in `platinum/`. Always unpartitioned (it cuts across all sources).
