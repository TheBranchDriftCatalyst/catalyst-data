# Speaker ID / Voice Profiling as Concordance Mechanism

Spike for **CD-40b**. Sibling: **CD-0sc** (text-based intra-source resolution). Epic: **CD-34j**.

## 1. Problem statement

The platinum canonical-entity layer cannot merge identities within a single code location. During the April 11 P0 recovery, run `3f58561a` produced `alignment_edges=0` on 15 media_ingest partitions (1514 candidates → 1320 singletons). `CrossSourceAligner.align` only emits edges across distinct source keys (`concordance.py:275-276`), so same-source duplicates (e.g. "Joe Biden" across 15 YouTube videos) never get compared. CD-0sc fixes the **text** side of this. CD-40b (this doc) fixes the **voice** side: pyannote currently emits file-local `SPEAKER_XX` labels (`diarization.py:129,150`) that reset per document. Speaker identity is a strictly stronger signal than name matching — if voice-A shows up in two transcripts, that is direct acoustic evidence of same-person, regardless of how the name is spelled or whether a name appears at all.

## 2. Current state

**Diarization** (`diarization.py:84-114`): `pyannote/speaker-diarization-3.1` loads per-partition; output is aligned to whisper segments via `_assign_speakers` (`:127-142`). Embeddings are computed internally by the pipeline but never surfaced. Runs CPU-only in the current k8s config (`:30-45`).

**Intra-doc concordance** (`concordance.py:77-230`): `ConcordanceEngine.resolve()` runs union-find over mentions with 4 passes (exact/substring/jaccard/embedding). Scoped to one code location at a time.

**Cross-source alignment** (`concordance.py:254-288`): double-loop over `locations[i+1:]` — structurally blind to same-source duplicates. Signal tracking at `:290-324` attaches `exact_name`/`substring`/`jaccard`/`embedding` to each edge.

## 3. Proposed architecture

### Q1. Gold asset, not platinum resolver
Add `media_speaker_profiles` as a **gold-layer asset in media_ingest**, partitioned-unpartitioned split (per-partition embedding extraction; one unpartitioned fan-in asset for clustering). Rationale: voice embedding is an ingestion concern that rides the existing pyannote model load — moving it to platinum would force a second audio read from NFS per partition. Platinum consumes the output, it doesn't compute it.

### Q2. Data flow

```
media_diarization (gold, partitioned)
        │ pyannote Annotation + segment timestamps
        ▼
media_speaker_embeddings (gold, partitioned)       ← NEW
        │ per-SPEAKER_XX centroid (mean of segment embeddings)
        │ schema: {partition_key, local_label, centroid[192], segment_count, total_duration_s}
        ▼
media_speaker_profiles (gold, unpartitioned, AllPartitionMapping fan-in)   ← NEW
        │ agglomerative clustering across all centroids, cosine metric
        │ emits SpeakerProfile: {profile_id, centroid[192], member_refs, first_seen, display_name=None}
        │ persists to pgvector table `speaker_profiles`
        ▼
media_entity_candidates (existing gold)
        │ augmented: each Mention with a timestamp range is tagged with profile_id
        │ new mention_type=SPEAKER creates an EntityCandidate per profile_id
        ▼
canonical_entities (platinum)
        │ consumes profile-tagged candidates; aligner sees the new signal
```

### Q3. New `AlignmentSignal` inside the existing scorer
Add `speaker_profile` as a fifth signal in `CrossSourceAligner._score_pair` (`concordance.py:290-324`). If two `EntityCandidate`s share a non-null `profile_id`, append `(0.92, "speaker_profile")`. This is the cleanest structural fit: speaker-profile matches compose with name matches under the existing combined-score rule (`max + 0.05/extra`), and the signal naturally propagates to CD-0sc's new intra-source path once that lands. A separate pre-merge step was considered but rejected — it would duplicate the union-find logic in `canonical_entities.py:142-149` and create a second provenance surface to debug.

**Coordination with CD-0sc**: CD-0sc adds `intra_source_align`; this spike plugs into the same `_score_pair` that intra- and cross-source both call. No interface collision.

### Q4. v1 quality bar
Ship behind `SPEAKER_PROFILE_ENABLED` flag with **agglomerative clustering, cosine distance, threshold 0.25** (≈ cosine similarity 0.75). Rationale: pyannote's own docs cite "typical threshold ~0.25 for same speaker" for their `pyannote/embedding` model. Minimum segment duration 3s (short segments are noisy). Graceful degradation: if a candidate has no `profile_id` (speaker too short, clustering failed, flag off), the aligner behaves exactly as today.

**No manual naming UI in v1.** Profiles are identified by stable `profile_id` hashes; human names can be attached later via a follow-up Streamlit page in data-explorer. Incorrect clusters are visible via platinum metrics (mention_count anomalies) and can be corrected post-hoc by splitting profiles.

### Q5. Relationship to CD-0sc
**Two independent tracks, one shared interface.** CD-0sc is text-side (adds `intra_source_align` to `CrossSourceAligner`); CD-40b is voice-side (adds a new signal consumed by the same scorer). They meet at `_score_pair`. Merging them into one design would delay CD-0sc, which is the smaller, higher-confidence fix. Ship CD-0sc first; this spike's tasks land behind it and automatically benefit from intra-source edges once both are live.

## 4. Technical stack choice

**Pick: pyannote built-in embeddings (`pyannote/embedding`, 192-d, via `PretrainedSpeakerEmbedding`).**

Evidence: ECAPA-TDNN wins on VoxCeleb EER (1.71% vs pyannote's ~3% per MDPI 2024 comparison, https://www.mdpi.com/2076-3417/14/4/1329), but the delta is moot for a 2-speaker-per-file podcast corpus where per-cluster centroids average out noise. Pyannote embeddings ride the existing HF model load and the existing `hf-credentials` secret (`diarization.py:41`), adding ~0 deployment surface. The speechbrain ECAPA path requires a second model, CUDA coordination, and a new k8s secret — not worth it for v1. We can swap the embedding backend later behind the same asset key if recall becomes a problem. Resemblyzer and WavLM ruled out: dated / research-grade respectively.

**Reference**: pyannote.audio docs expose `SpeakerEmbedding` and `PretrainedSpeakerEmbedding` directly (see https://github.com/pyannote/pyannote-audio README + `pipelines/speaker_verification.py`). Current release: 3.x series (3.1 is what's pinned in `diarization.py:100`). Don't inline API details here — they drift.

## 5. Storage layer

**Pick: pgvector in `postgres_knowledge`.** Same stack as `canonical_entities` (`resources.py:60-100`), reuses existing connection pool and migration tooling. Neo4j vector index rejected: adds a second write path for no traversal win (profiles don't have rich relationships yet). Filesystem jsonl rejected: can't do approximate-nearest-neighbor at query time.

Schema:

```sql
CREATE TABLE speaker_profiles (
  profile_id       TEXT PRIMARY KEY,            -- sha1(centroid + first_seen)
  centroid         vector(192) NOT NULL,
  display_name     TEXT,                        -- nullable, set via future UI
  member_count     INT NOT NULL DEFAULT 0,
  total_duration_s REAL NOT NULL DEFAULT 0,
  first_seen       TIMESTAMPTZ NOT NULL,
  last_seen        TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON speaker_profiles USING ivfflat (centroid vector_cosine_ops);

CREATE TABLE speaker_profile_members (
  profile_id    TEXT REFERENCES speaker_profiles(profile_id),
  document_id   TEXT NOT NULL,
  local_label   TEXT NOT NULL,                  -- pyannote's SPEAKER_XX
  segment_count INT NOT NULL,
  PRIMARY KEY (profile_id, document_id, local_label)
);
```

## 6. Risks + unknowns

1. **Low-SNR podcast audio** — pyannote embeddings degrade on music beds, phone call segments. Mitigation: filter segments by pyannote's built-in quality score; log per-profile avg-quality as a metric.
2. **Cluster drift over months** — agglomerative re-clusters from scratch each run; IDs may shift. Mitigation: make re-clustering sticky (prefer merging into existing profiles by nearest-centroid before creating new ones).
3. **Voice overlap** — two speakers talking simultaneously yields a blended embedding. pyannote's overlap-aware diarization helps, but isn't perfect. Accept as v1 noise.
4. **Threshold calibration** — 0.25 is a pyannote default, not our corpus. First month in production should ship with a dashboard showing cosine-distance histogram of merged vs split clusters for manual tuning.
5. **GPU contention** — CD-q8t (XPU diarization) is in flight. Speaker embedding should piggyback on that same pipeline run, not add a second model load.

## 7. Follow-up tasks

- **CD-34j.1** Ingestion-side: extract per-segment pyannote embeddings during diarization, compute per-`SPEAKER_XX` centroids, add `media_speaker_embeddings` (partitioned) and `media_speaker_profiles` (unpartitioned fan-in) assets with pgvector persistence. Depends on: nothing. Unblocks CD-34j.2. Piggyback on CD-q8t GPU work.
- **CD-34j.2** Platinum-side: add `speaker_profile` as a 5th signal in `CrossSourceAligner._score_pair` (`concordance.py:290-324`), tag `EntityCandidate` with `profile_id` where timestamps permit, behind `SPEAKER_PROFILE_ENABLED` flag. Depends on: CD-34j.1 + CD-0sc.
- **CD-34j.3** (optional) data-explorer Streamlit page for manual profile naming with merge/split controls.
