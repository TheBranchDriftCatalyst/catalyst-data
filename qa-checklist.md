# Materialization QA Checklist

**Date**: 2026-03-15
**Cluster**: catalyst-cluster (talos00)
**Total Assets**: 34 (32 materialized, 2 unmaterialized — graph visualization assets)

## Status Legend
- [ ] Not tested
- [x] PASS
- [!] FAIL (see notes)
- [~] SKIP (expected)
- OLD — Superseded by newer EDC assets; still materialized but no longer on critical path
- NEW — Added in EDC (Entity-Driven Consolidation) pipeline update

---

## Pipeline 1: Congress Data (12 assets)

### Bronze Layer (API extraction) — 3/3 materialized

| # | Asset | Status | Acceptance Criteria | Notes |
|---|-------|--------|---------------------|-------|
| 1 | `congress_bills` | [x] | Runs; returns list[Bill]; count > 0; metadata has bill_count | 25 bills, congress 118 |
| 2 | `congress_members` | [x] | Runs; returns list[Member]; count > 0; metadata has member_count | 25 members |
| 3 | `congress_committees` | [x] | Runs; returns list[Committee]; count > 0; metadata has committee_count | 25 committees |

### Silver Layer (transform + chunk) — 3/3 materialized

| # | Asset | Status | Upstream | Acceptance Criteria | Notes |
|---|-------|--------|----------|---------------------|-------|
| 4 | `congress_documents` | [x] | bills, members, committees | Runs; returns list[Document]; each has id, title, content, document_type; metadata has document_count, by_type breakdown | 75 documents (25 each type) |
| 5 | `congress_chunks` | [x] | documents | Runs; returns list[TextChunk]; chunk_count > 0; bills chunked at 400/100; members/committees passthrough; metadata has chunks_by_type | 75 chunks |
| 6 | `congress_entities` | [x] | LLM (LiteLLM) | Runs; returns list[dict] with entities; each has text, label, context; metadata has chunk_count, entity_count | 202 entities — **OLD**, superseded by `congress_mentions` |

### Gold Layer (LLM extraction + embeddings) — 6/7 materialized

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 7 | `congress_mentions` | [x] | LLM (LiteLLM) | Runs; returns entity mentions with spans and types; linked to chunks | **NEW EDC** — replaces `congress_entities` |
| 8 | `congress_entity_candidates` | [x] | LLM (LiteLLM) | Runs; returns deduplicated entity candidates from mentions | **NEW EDC** |
| 9 | `congress_assertions` | [x] | LLM (LiteLLM) | Runs; returns structured assertions (subject-predicate-object with provenance) | **NEW EDC** — replaces `congress_propositions` |
| 10 | `congress_propositions` | [x] | LLM (LiteLLM) | Runs; returns SPO triples with confidence; metadata has proposition_count | 146 propositions — **OLD**, superseded by `congress_assertions` |
| 11 | `congress_embeddings` | [x] | Embeddings (LiteLLM) | Runs; returns embeddings with correct dimensions; metadata has chunk_count, model | 75 embeddings, text-embedding-3-small, 1536 dims |
| 12 | `congress_graph` | [~] | Neo4j | Graph visualization asset; requires Neo4j + populated platinum layer | **NOT MATERIALIZED** — Neo4j deployed but graph asset not yet run |

---

## Pipeline 2: Open Leaks (13 assets)

### Bronze Layer (extraction) — 4/4 materialized

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 13 | `wikileaks_cables` | [x] | archive.org HTTP | Runs; returns list[Cable]; count > 0; each has subject, body, date, classification | 50 cables |
| 14 | `icij_offshore_entities` | [x] | ICIJ HTTP + ZIP | Runs; returns list[OffshoreEntity]; count > 0; each has name, jurisdiction, source_dataset | 50 entities (Panama Papers) |
| 15 | `icij_offshore_relationships` | [x] | ICIJ HTTP + ZIP | Runs; returns list[OffshoreRelationship]; count > 0; each has node_id_start, node_id_end, rel_type | 50 relationships |
| 16 | `epstein_court_docs` | [x] | epsteininvestigation.org API | Runs; returns list[CourtDocument]; count > 0; each has title, content, date | 25 docs (1 fbi_interview, 24 foia_release) |

### Silver Layer — 2/2 materialized

| # | Asset | Status | Upstream | Acceptance Criteria | Notes |
|---|-------|--------|----------|---------------------|-------|
| 17 | `leak_documents` | [x] | cables, offshore_entities, court_docs | Runs; returns list[Document]; has all 3 document_types; metadata has document_count, by_type | 125 documents |
| 18 | `leak_chunks` | [x] | documents | Runs; returns list[TextChunk]; cables chunked at 1500/250; court_docs at 2000/400; offshore_entities passthrough; metadata has chunks_by_type | 356 chunks (281 cable, 25 court, 50 offshore) |

### Gold Layer — 6/7 materialized

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 19 | `leak_mentions` | [x] | LLM (LiteLLM) | Runs; returns entity mentions with spans and types | **NEW EDC** — replaces `leak_entities` |
| 20 | `leak_entity_candidates` | [x] | LLM (LiteLLM) | Runs; returns deduplicated entity candidates from mentions | **NEW EDC** |
| 21 | `leak_assertions` | [x] | LLM (LiteLLM) | Runs; returns structured assertions with provenance | **NEW EDC** — replaces `leak_propositions` |
| 22 | `leak_entities` | [x] | LLM (LiteLLM) | Runs; returns entities with text, label, context; metadata has entity_count | 3268 entities — **OLD**, superseded by `leak_mentions` |
| 23 | `leak_propositions` | [x] | LLM (LiteLLM) | Runs; returns SPO triples with confidence; metadata has proposition_count | 2596 propositions — **OLD**, superseded by `leak_assertions` |
| 24 | `leak_embeddings` | [x] | Embeddings (LiteLLM) | Runs; returns embeddings with correct dimensions; metadata has chunk_count, model | 356 embeddings, text-embedding-3-small, 1536 dims |
| 25 | `leak_graph` | [~] | Neo4j | Graph visualization asset; requires Neo4j + populated platinum layer | **NOT MATERIALIZED** — Neo4j deployed but graph asset not yet run |

---

## Pipeline 3: Media Ingest (6 assets)

### Bronze Layer — 1/1 materialized

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 26 | `media_files` | [x] | NFS filesystem | Runs; returns list[dict] with file paths, sizes, types; count > 0; scans metube + tubesync dirs | 5 files, 1.61 GiB total, all .mp4 from metube |

### Silver Layer — 5/5 materialized

| # | Asset | Status | Upstream | Acceptance Criteria | Notes |
|---|-------|--------|----------|---------------------|-------|
| 27 | `media_metadata` | [x] | media_files | Runs; returns enriched dicts with ffprobe metadata (duration, codecs, resolution); metadata has file_count, skipped | 5 files, 2.59 hours total |
| 28 | `media_documents` | [x] | media_metadata | Runs; returns list[MediaDocument]; each has id, title, content_type, metadata | 5 documents |
| 29 | `media_transcriptions` | [x] | media_documents | Runs; returns list[dict] with text, language, segments; skips non-audio; metadata has transcription_count | 5 transcribed, language=en |
| 30 | `media_chunks` | [x] | transcriptions | Runs; returns list[TextChunk]; chunked at 800/150; metadata has chunk_count, skipped | 234 chunks (800/150) |
| 31 | `media_embeddings` | [x] | Embeddings (LiteLLM) | Runs; returns embeddings with correct dimensions; metadata has chunk_count, model | 234 embeddings, text-embedding-3-small, 1536 dims |

> **Gap**: media_ingest does not yet have EDC gold assets (no `media_mentions`, `media_entity_candidates`, `media_assertions`). These should be added to bring media_ingest to parity with congress and open_leaks pipelines.

---

## Pipeline 4: Knowledge Graph — Platinum Layer (3 assets, NEW)

Cross-source consolidation layer built on top of Gold EDC outputs from congress and open_leaks.

### Platinum Layer — 3/3 materialized

| # | Asset | Status | Upstream | Acceptance Criteria | Notes |
|---|-------|--------|----------|---------------------|-------|
| 32 | `canonical_entities` | [x] | congress_entity_candidates, leak_entity_candidates | Runs; produces deduplicated canonical entity set across all sources | 3570 canonical entities — **NEW** |
| 33 | `entity_alignments` | [x] | canonical_entities | Runs; produces entity alignment edges (sameAs / possibleSameAs) | 507 edges (15 sameAs, 492 possibleSameAs) — **NEW** |
| 34 | `assertion_graph` | [x] | canonical_entities, entity_alignments, congress_assertions, leak_assertions | Runs; builds unified assertion graph linking assertions to canonical entities | 4393 assertions, 4072 fully linked — **NEW** |

---

## Testing Phases

### Phase 1: Bronze — external data sources
Test connectivity and data extraction first. No upstream deps.

- [x] Congress bronze (parallel): `congress_bills`, `congress_members`, `congress_committees`
- [x] Leaks bronze (parallel): `wikileaks_cables`, `icij_offshore_entities`, `icij_offshore_relationships`, `epstein_court_docs`
- [x] Media bronze: `media_files`

### Phase 2: Silver — transforms + documents
- [x] `congress_documents`
- [x] `leak_documents`
- [x] `media_metadata` -> `media_documents` (sequential)

### Phase 3: Chunking
- [x] `congress_chunks`
- [x] `leak_chunks`
- [x] `media_transcriptions` -> `media_chunks` (sequential, Whisper is slow)

### Phase 4: Legacy LLM + Embeddings
- [x] NER (OLD): `congress_entities`, `leak_entities` (parallel) — superseded by EDC mentions
- [x] Propositions (OLD): `congress_propositions`, `leak_propositions` (parallel) — superseded by EDC assertions
- [x] Embeddings: `congress_embeddings`, `leak_embeddings`, `media_embeddings` (parallel)

### Phase 5: Gold EDC (Entity-Driven Consolidation)
- [x] Mentions: `congress_mentions`, `leak_mentions` (parallel)
- [x] Entity candidates: `congress_entity_candidates`, `leak_entity_candidates` (parallel)
- [x] Assertions: `congress_assertions`, `leak_assertions` (parallel)

### Phase 6: Platinum — Knowledge Graph Consolidation
- [x] `canonical_entities` — cross-source entity deduplication
- [x] `entity_alignments` — sameAs / possibleSameAs linking
- [x] `assertion_graph` — unified assertion graph

### Phase 7: Graph Visualization — NOT YET RUN
- [~] `congress_graph` — Neo4j deployed but asset not materialized
- [~] `leak_graph` — Neo4j deployed but asset not materialized

---

## Known Risks
- **Congress API rate limits** — reduce `max_results` in launchpad config if needed
- **ICIJ ZIP download** — large file (~500MB), may time out
- **Whisper transcription** — CPU-intensive, limit to 1-2 files for testing
- **LLM rate limits** — LiteLLM proxy may throttle; test with small chunk counts
- **Epstein API** — third-party, may be down or rate-limited
- **NFS mounts** — media-ingest needs metube/tubesync NFS volumes accessible
- **S3 path code_location=default** — asset S3 paths used `code_location=default` before `DAGSTER_CODE_LOCATION` env var was set; older materializations may reference stale paths
- **FK constraint on knowledge graph** — platinum assets writing to PostgreSQL+pgvector may hit FK constraint errors if entity_candidates upstream changed without re-running downstream
- **open_leaks code location not registered** — open_leaks code location may not be currently registered in Dagster; requires Dagster restart to pick up. Leak assets may not appear in UI until resolved.
- **media_ingest EDC gap** — no gold EDC assets (mentions, entity_candidates, assertions) exist for media_ingest yet; media data is not included in platinum layer consolidation

---

## Infrastructure

| Component | Endpoint | Status |
|-----------|----------|--------|
| Dagster webserver | dagster.talos00 | Running |
| Neo4j | neo4j.catalyst-data.svc.cluster.local:7687 | Deployed |
| PostgreSQL+pgvector | postgres-knowledge.catalyst-data.svc.cluster.local:5432 | Running |
| MinIO (S3) | minio.catalyst-data.svc.cluster.local:9000 | Running |
| LiteLLM proxy | litellm.catalyst-data.svc.cluster.local:4000 | Running |

---

## Verification Method

For each asset materialization:
1. Trigger via Dagster UI at `dagster.talos00` (Launchpad -> select asset -> Materialize)
2. Check run status — must be **SUCCESS**
3. Check pod logs: `kubectl logs -n catalyst-data <pod> --tail=50`
4. Verify materialization metadata in Dagster UI run details (counts, stats)
5. Check MinIO S3 for persisted output if applicable

---

## Test Results Log

**Tested**: 2026-03-15 (updated from 2026-03-12)
**Result**: 32/34 materialized — 32 PASS, 2 NOT MATERIALIZED (graph visualization assets)

### Infrastructure Issues Found & Fixed (Phase 1-4, 2026-03-12)

| Issue | Fix | Commit |
|-------|-----|--------|
| `CONGRESS_API_KEY` missing in run pods | Added `congress-data-secrets` to `envFrom` in dagster-instance.yaml | `e6d1456` |
| MinIO bucket `catalyst-data` didn't exist | Created via `aws s3 mb` in temporary pod | (runtime) |
| `ImagePullBackOff` on run pods (GHCR private) | Added `image_pull_secrets: [{name: ghcr-secret}]` to pod_spec_config | `453641a` |
| `date_filed=None` from Epstein API | `item.get("document_date") or ""` (key exists but value is None) | `453641a` |
| `district` returned as int from Congress API | `str(data["district"]) if data.get("district") is not None else None` | `0f114ea` |
| IO manager deserializes JSONL as list[dict] not list[Model] | Added `type_hint` param to `deserialize()`, reconstruct Pydantic models via `model_validate()` | `4706a01` |
| NFS PVC name mismatch in media-ingest | Corrected `media-ingest-metube-downloads` → `metube-downloads` | `e6d1456` |
| Stale cached images on K8s nodes | Added `image_pull_policy: Always` to container_config | `3647ad1` |

### Infrastructure Issues Found (Phase 5-6, EDC deployment, 2026-03-15)

| Issue | Status | Notes |
|-------|--------|-------|
| S3 paths defaulted to `code_location=default` | Fixed | `DAGSTER_CODE_LOCATION` env var now set on all deployments |
| open_leaks code location not registered in Dagster | Open | May need Dagster restart to pick up; leak assets may not show in UI |
| FK constraint errors in platinum PostgreSQL writes | Observed | Occurs when upstream entity_candidates change without downstream re-run |

### Phase 1 Results — Bronze
All 8 bronze assets PASS.

| Asset | Count | Notes |
|-------|-------|-------|
| `congress_bills` | 25 | Congress 118 |
| `congress_members` | 25 | |
| `congress_committees` | 25 | |
| `wikileaks_cables` | 50 | |
| `icij_offshore_entities` | 50 | Panama Papers |
| `icij_offshore_relationships` | 50 | |
| `epstein_court_docs` | 25 | 1 fbi_interview, 24 foia_release |
| `media_files` | 5 | 1.61 GiB total, all .mp4 from metube |

### Phase 2 Results — Silver Transforms
All 4 silver transform assets PASS.

| Asset | Count | Notes |
|-------|-------|-------|
| `congress_documents` | 75 | 25 each type (bill, member, committee) |
| `leak_documents` | 125 | cables + offshore + court |
| `media_metadata` | 5 | 2.59 hours total duration |
| `media_documents` | 5 | |

### Phase 3 Results — Chunking
All 4 chunking assets PASS.

| Asset | Count | Notes |
|-------|-------|-------|
| `congress_chunks` | 75 | |
| `leak_chunks` | 356 | 281 cable, 25 court, 50 offshore |
| `media_transcriptions` | 5 | language=en |
| `media_chunks` | 234 | 800/150 chunk/overlap |

### Phase 4 Results — Legacy LLM + Embeddings
All 7 LLM/embedding assets PASS. NER and propositions are OLD, superseded by EDC.

| Asset | Count | Notes |
|-------|-------|-------|
| `congress_entities` | 202 | NER extraction — **OLD** |
| `congress_propositions` | 146 | SPO triples — **OLD** |
| `congress_embeddings` | 75 | text-embedding-3-small, 1536 dims |
| `leak_entities` | 3268 | NER extraction — **OLD** |
| `leak_propositions` | 2596 | SPO triples — **OLD** |
| `leak_embeddings` | 356 | text-embedding-3-small, 1536 dims |
| `media_embeddings` | 234 | text-embedding-3-small, 1536 dims |

### Phase 5 Results — Gold EDC
All 6 EDC gold assets PASS.

| Asset | Notes |
|-------|-------|
| `congress_mentions` | **NEW** — replaces congress_entities |
| `congress_entity_candidates` | **NEW** |
| `congress_assertions` | **NEW** — replaces congress_propositions |
| `leak_mentions` | **NEW** — replaces leak_entities |
| `leak_entity_candidates` | **NEW** |
| `leak_assertions` | **NEW** — replaces leak_propositions |

### Phase 6 Results — Platinum
All 3 platinum assets PASS.

| Asset | Count | Notes |
|-------|-------|-------|
| `canonical_entities` | 3570 | Cross-source deduplication |
| `entity_alignments` | 507 edges | 15 sameAs, 492 possibleSameAs |
| `assertion_graph` | 4393 assertions | 4072 fully linked to canonical entities |

### Phase 7 — Graph Visualization (NOT YET RUN)
- `congress_graph` — Neo4j is deployed but graph asset not yet materialized
- `leak_graph` — Neo4j is deployed but graph asset not yet materialized
