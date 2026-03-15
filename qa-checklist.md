# Materialization QA Checklist

**Date**: 2026-03-12
**Cluster**: catalyst-cluster (talos00)
**Total Assets**: 25 (23 testable, 2 skipped — Neo4j graph assets)

## Status Legend
- [ ] Not tested
- [x] PASS
- [!] FAIL (see notes)
- [~] SKIP (expected)

---

## Pipeline 1: Congress Data (9 assets)

### Bronze Layer (API extraction)

| # | Asset | Status | Acceptance Criteria | Notes |
|---|-------|--------|---------------------|-------|
| 1 | `congress_bills` | [x] | Runs; returns list[Bill]; count > 0; metadata has bill_count | 25 bills, congress 118 |
| 2 | `congress_members` | [x] | Runs; returns list[Member]; count > 0; metadata has member_count | 25 members |
| 3 | `congress_committees` | [x] | Runs; returns list[Committee]; count > 0; metadata has committee_count | 25 committees |

### Silver Layer (transform + chunk)

| # | Asset | Status | Upstream | Acceptance Criteria | Notes |
|---|-------|--------|----------|---------------------|-------|
| 4 | `congress_documents` | [x] | bills, members, committees | Runs; returns list[Document]; each has id, title, content, document_type; metadata has document_count, by_type breakdown | 75 documents (25 each type) |
| 5 | `congress_chunks` | [x] | documents | Runs; returns list[TextChunk]; chunk_count > 0; bills chunked at 400/100; members/committees passthrough; metadata has chunks_by_type | 75 chunks |

### Silver/Gold Layer (LLM + embeddings)

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 6 | `congress_entities` | [x] | LLM (LiteLLM) | Runs; returns list[dict] with entities; each has text, label, context; metadata has chunk_count, entity_count | 202 entities |
| 7 | `congress_propositions` | [x] | LLM (LiteLLM) | Runs; returns list[dict] with SPO triples; each has subject, predicate, object, confidence; metadata has chunk_count, proposition_count | 146 propositions |
| 8 | `congress_embeddings` | [x] | Embeddings (LiteLLM) | Runs; returns list[dict] with chunk_id, embedding, model, dimensions; embedding length matches dimensions; metadata has chunk_count, model | 75 embeddings, text-embedding-3-small, 1536 dims |
| 9 | `congress_graph` | [~] | Neo4j | **SKIP** — stubbed, requires Neo4j | |

---

## Pipeline 2: Open Leaks (10 assets)

### Bronze Layer (extraction)

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 10 | `wikileaks_cables` | [x] | archive.org HTTP | Runs; returns list[Cable]; count > 0; each has subject, body, date, classification | 50 cables |
| 11 | `icij_offshore_entities` | [x] | ICIJ HTTP + ZIP | Runs; returns list[OffshoreEntity]; count > 0; each has name, jurisdiction, source_dataset | 50 entities (Panama Papers) |
| 12 | `icij_offshore_relationships` | [x] | ICIJ HTTP + ZIP | Runs; returns list[OffshoreRelationship]; count > 0; each has node_id_start, node_id_end, rel_type | 50 relationships |
| 13 | `epstein_court_docs` | [x] | epsteininvestigation.org API | Runs; returns list[CourtDocument]; count > 0; each has title, content, date | 25 docs (1 fbi_interview, 24 foia_release) |

### Silver Layer

| # | Asset | Status | Upstream | Acceptance Criteria | Notes |
|---|-------|--------|----------|---------------------|-------|
| 14 | `leak_documents` | [x] | cables, offshore_entities, court_docs | Runs; returns list[Document]; has all 3 document_types; metadata has document_count, by_type | 125 documents |
| 15 | `leak_chunks` | [x] | documents | Runs; returns list[TextChunk]; cables chunked at 1500/250; court_docs at 2000/400; offshore_entities passthrough; metadata has chunks_by_type | 356 chunks (281 cable, 25 court, 50 offshore) |

### Silver/Gold Layer

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 16 | `leak_entities` | [x] | LLM (LiteLLM) | Runs; returns entities with text, label, context; metadata has entity_count | 3268 entities |
| 17 | `leak_propositions` | [x] | LLM (LiteLLM) | Runs; returns SPO triples with confidence; metadata has proposition_count | 2596 propositions |
| 18 | `leak_embeddings` | [x] | Embeddings (LiteLLM) | Runs; returns embeddings with correct dimensions; metadata has chunk_count, model | 356 embeddings, text-embedding-3-small, 1536 dims |
| 19 | `leak_graph` | [~] | Neo4j | **SKIP** — stubbed, requires Neo4j | |

---

## Pipeline 3: Media Ingest (6 assets)

### Bronze Layer

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 20 | `media_files` | [x] | NFS filesystem | Runs; returns list[dict] with file paths, sizes, types; count > 0; scans metube + tubesync dirs | 5 files, 1.61 GiB total, all .mp4 from metube |

### Silver Layer

| # | Asset | Status | Upstream | Acceptance Criteria | Notes |
|---|-------|--------|----------|---------------------|-------|
| 21 | `media_metadata` | [x] | media_files | Runs; returns enriched dicts with ffprobe metadata (duration, codecs, resolution); metadata has file_count, skipped | 5 files, 2.59 hours total |
| 22 | `media_documents` | [x] | media_metadata | Runs; returns list[MediaDocument]; each has id, title, content_type, metadata | 5 documents |
| 23 | `media_transcriptions` | [x] | media_documents | Runs; returns list[dict] with text, language, segments; skips non-audio; metadata has transcription_count | 5 transcribed, language=en |

### Silver/Gold Layer

| # | Asset | Status | External Dep | Acceptance Criteria | Notes |
|---|-------|--------|-------------|---------------------|-------|
| 24 | `media_chunks` | [x] | transcriptions | Runs; returns list[TextChunk]; chunked at 800/150; metadata has chunk_count, skipped | 234 chunks (800/150) |
| 25 | `media_embeddings` | [x] | Embeddings (LiteLLM) | Runs; returns embeddings with correct dimensions; metadata has chunk_count, model | 234 embeddings, text-embedding-3-small, 1536 dims |

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

### Phase 4: LLM + Embeddings
- [x] NER: `congress_entities`, `leak_entities` (parallel)
- [x] Propositions: `congress_propositions`, `leak_propositions` (parallel)
- [x] Embeddings: `congress_embeddings`, `leak_embeddings`, `media_embeddings` (parallel)

### Phase 5: Graph — SKIP
- [~] `congress_graph` — Neo4j not deployed
- [~] `leak_graph` — Neo4j not deployed

---

## Known Risks
- **Congress API rate limits** — reduce `max_results` in launchpad config if needed
- **ICIJ ZIP download** — large file (~500MB), may time out
- **Whisper transcription** — CPU-intensive, limit to 1-2 files for testing
- **LLM rate limits** — LiteLLM proxy may throttle; test with small chunk counts
- **Epstein API** — third-party, may be down or rate-limited
- **NFS mounts** — media-ingest needs metube/tubesync NFS volumes accessible

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

**Tested**: 2026-03-12
**Result**: 23/23 PASS, 2 SKIP (graph assets — Neo4j not deployed)

### Infrastructure Issues Found & Fixed

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

### Phase 4 Results — LLM + Embeddings
All 7 LLM/embedding assets PASS.

| Asset | Count | Notes |
|-------|-------|-------|
| `congress_entities` | 202 | NER extraction |
| `congress_propositions` | 146 | SPO triples |
| `congress_embeddings` | 75 | text-embedding-3-small, 1536 dims |
| `leak_entities` | 3268 | NER extraction |
| `leak_propositions` | 2596 | SPO triples |
| `leak_embeddings` | 356 | text-embedding-3-small, 1536 dims |
| `media_embeddings` | 234 | text-embedding-3-small, 1536 dims |

### Phase 5 — Graph (SKIPPED)
- `congress_graph` — SKIP (Neo4j not deployed)
- `leak_graph` — SKIP (Neo4j not deployed)
