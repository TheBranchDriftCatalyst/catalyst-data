# Seed Generation — Ground-Truth Candidate Sampling

This document explains how `scripts/benchmark/sample_gt_candidates.py` picks the
chunks that get human-annotated for benchmarking and SFT/DPO training.
The output (`.test-output/gt-candidates.json`) is the **seed** that
drives the entire RLHF loop — get this set wrong and every downstream
metric is biased.

---

## Why we don't just pick chunks randomly

The benchmark today has 10 curated chunks. SFT/DPO fine-tuning wants
200+. Three naive strategies fail in different ways:

- **First-N** — biases toward intros / cold-opens / preambles. The
  benchmark trains the LLM to extract from the *beginning* of every
  document.
- **Uniform random** — over-samples the dominant content shape. Open-leaks
  produces 3.6M chunks; ~2M are ICIJ corporate-entity passthroughs that
  all look the same. A uniform random 60-chunk sample picks ~33 of those.
- **Pure embedding-distance diversity** — picks chunks that are
  *topically* distinct but extractively useless (talk-show banter, page
  headers, boilerplate "all rights reserved" in cables). The chunks span
  embedding space but don't span the model's *extraction* failure modes.

The sampler combines three signals to do better:

1. **Stratify by domain** so a single corpus can't dominate.
2. **Drop extractively-empty chunks** (optional GLiNER NER pass) so a human
   isn't annotating chunks the model has nothing to find in.
3. **Diversify on a feature vector that mixes structural attributes
   (speaker, chunk strategy, document type) with mention-type histogram
   and text embedding** — distance reflects "would these two chunks
   exercise the model differently?" not just "do they talk about
   different things?"

---

## Pipeline

Single command, deterministic given `--seed`:

```bash
python scripts/benchmark/sample_gt_candidates.py --target 200 --seed 42
```

Per-domain stages:

```
load_chunks()                    # tests/shared/medallion.py — globs all materialized chunks
    ↓
bucket by domain                 # path-based → {media-ingest, congress-data, open-leaks, …}
    ↓
[per domain, in registration order:]
    ↓
prefilter (DomainSpec.prefilter_max)  # random down-sample for huge corpora; open-leaks 3.6M → 5000
    ↓
NER pre-filter (--score-extractions)  # GLiNER cheap pass; drop chunks with 0 mentions
    ↓
embed                            # dagster_io.EmbeddingResource (OpenAI / sentence-transformers)
    ↓
build feature vector             # [categorical | scalar | mention-hist | weight·embedding]
    ↓
greedy farthest-point sampling   # k-center: O(N·k); deterministic given a sub-seed
    ↓
write .test-output/gt-candidates.json
```

Each stage is independently cacheable: NER results live in
`.test-output/gt-sampler-cache/<domain>/ner_pass.json`, embeddings in
`<domain>/embeddings.npz`. Re-runs after the first invocation are
seconds, not minutes.

---

## Determinism contract

Every randomness call is seeded from a single `--seed` value via
`_seed_for(seed, label)`, which combines the user seed with a stable
SHA-256 hash of the stage label:

```python
def _seed_for(seed: int, label: str) -> int:
    return (seed * 1_000_003 + _stable_hash_int(label)) % (2**32)
```

So:

- `_seed_for(42, "media-ingest:prefilter")` ≠ `_seed_for(42, "media-ingest:fps")`
- Each domain × stage gets an independent RNG.
- Running with the same `--seed` produces a byte-identical
  `gt-candidates.json` regardless of:
  - Python's `PYTHONHASHSEED`
  - Set/dict iteration order (sampled indices are sorted before output)
  - Cache state (cached vs cold runs converge once caches warm)
  - Filesystem glob order (`load_chunks` results are stable-sorted by
    `chunk_id` before bucketing)

**Verification:** the sampler is part of the determinism contract — if
you change the algorithm, prove it by:

```bash
python scripts/benchmark/sample_gt_candidates.py --target 200 --seed 42 --output /tmp/a.json
python scripts/benchmark/sample_gt_candidates.py --target 200 --seed 42 --output /tmp/b.json
diff /tmp/a.json /tmp/b.json   # must be empty
```

If determinism breaks, regression-test it in
`packages/congress-data/tests/test_bill_chunker.py`-style: a tiny unit
test that hashes the output JSON.

---

## Adding a new domain

The sampler is registry-driven. Adding a domain takes one
`register_domain(...)` call near the top of the script — no core
pipeline changes.

```python
register_domain(
    DomainSpec(
        name="senate-records",        # exact dir name under .test-output/
        default_quota=40,             # default samples for this domain
        categorical_features=(
            CategoricalFeature(
                key="committee",
                get=lambda c: (c.get("metadata", {}) or {}).get("committee") or "unknown",
            ),
            CategoricalFeature(
                key="document_kind",  # hearing | report | floor_speech
                get=lambda c: (c.get("metadata", {}) or {}).get("document_kind") or "unknown",
            ),
        ),
        scalar_features=(
            ScalarFeature(
                key="speaker_count",
                get=lambda c: float((c.get("metadata", {}) or {}).get("speaker_count", 1) or 1),
            ),
        ),
        prefilter_max=10_000,         # if the corpus is huge, set this; else None
    )
)
```

What the registry buys you:

- Argparse auto-adds `--senate-records N` flag for that quota
- Diagnostics auto-reports per-feature coverage
- Output schema includes the new domain in `quotas` and `diagnostics`
- No `if domain == "senate-records"` branches anywhere

What it doesn't do (yet):

- The new domain still needs to be in `tests/shared/medallion.py::_PATTERNS`
  if its chunks live somewhere unusual on disk
- You still write the integration test that materializes chunks (one of
  the existing patterns: per-domain conftest + `dagster.materialize`)

---

## Picking good per-domain features

Goal: the feature vector should make extractively-similar chunks **near**
each other and extractively-different chunks **far**. Greedy farthest-point
then naturally spreads the sample.

Heuristics for choosing what to include:

| Domain shape | Likely features |
|---|---|
| Multi-speaker dialogue | `primary_speaker` (one-hot), `speaker_count` (scalar) |
| Hierarchical document (bills, reports) | `chunk_strategy` (one-hot), `section_depth` (scalar) |
| Multi-source corpus (cables / entities / docs) | `document_type` or `source` (one-hot) |
| Time-stamped feed (news, sensor) | bucket the timestamp into eras (one-hot) |

What **NOT** to include:

- High-cardinality identifiers (`document_id`, `chunk_id`) — every
  chunk gets its own one-hot, blowing up the vector with no
  discriminative power
- Continuous attributes that swamp the embedding by magnitude — always
  normalize scalars to `[0, 1]` (the registry does this for you)
- Things that correlate near-perfectly with already-included features
  (e.g. `speaker_index` *and* `primary_speaker` would double-count)

The mention-type histogram (added when `--score-extractions` is on) is
domain-agnostic — it's the same vocab everywhere — so you don't have to
register it per domain.

---

## Two-stage subsampling for huge corpora

`DomainSpec.prefilter_max` exists because embedding 3.6M open-leaks
chunks would cost ~$200 in OpenAI tokens and take ~30 minutes. The
prefilter:

1. Randomly down-samples to `prefilter_max` chunks (default 5000) using
   the per-domain seed.
2. THEN runs the NER + embedding + farthest-point pipeline.

This is acceptable bias: the prefilter is uniform random over the full
corpus, so the 5000-chunk pool is statistically representative. The
downstream diversity sampling then picks 60 spread across the
representative pool. The overall sample is still well-distributed but
we only embed what we need.

If a domain is small (`< prefilter_max`), the prefilter is a no-op.

---

## Modes

- **embedding-only** (default, fast): no GLiNER pass; feature vector is
  `[categorical | scalar | embedding]`. ~30 seconds end-to-end after
  caches warm. Acceptable when the goal is "diversify across topics."

- **extraction-aware** (`--score-extractions`, recommended): runs
  GLiNER on the candidate pool first, drops chunks with zero mentions,
  and adds a normalized mention-type histogram to the feature vector.
  ~5-10 min on first run for the NER pass; instant after the cache
  exists. Use this for any sample that will be human-annotated.

- **--diagnostics**: prints per-domain feature coverage. Lets you see
  whether the sample actually captures the variability of the pool —
  e.g. `mention_type_coverage: {pool_distinct: 47, sample_distinct: 23,
  coverage_pct: 49}` means we covered ~half the mention-type vocabulary,
  which might suggest bumping the per-domain quota or weighting the
  histogram block more heavily (`--embedding-weight 0.5` halves the
  embedding block's pull).

---

## Output schema

```json
{
  "schema_version": "2",
  "seed": 42,
  "target": 200,
  "quotas": {"media-ingest": 80, "congress-data": 60, "open-leaks": 60},
  "score_extractions": false,
  "embedding_weight": 1.0,
  "candidates": [
    {"domain": "media-ingest", "document_id": "demo-video", "chunk_id": "demo-video:7", "index": 7},
    …
  ],
  "diagnostics": {"media-ingest": {"pool_size": 28, "selected_size": 20, …}, …},
  "total_selected": 199
}
```

Downstream consumers:

- **GT generation** — `task bench:ground-truth` reads this file
  automatically when present and restricts ensemble consensus voting to
  these chunk_ids. Pass `--no-candidates` to ignore the file and run
  against the full pool. (Shipped CD-4ssa.)
- **Viewer-ui GT editor** — annotation queue surfaces a chunk-list filter
  + j/k keyboard nav + debounced autosave so a human can review the 200.
  (Shipped CD-991f.) Per-chunk reviewed flag + diff-vs-ensemble views
  remain follow-ups.
- **SFT/DPO export** (CD-foy3) — training-data generator reads the
  reviewed GT and produces JSONL.

---

## Closing the GT loop end-to-end

Five commands take you from raw fixtures to a scored benchmark report:

```bash
# 1. Materialize chunks for all 3 domains via LocalJsonIOManager.
#    Outputs land at .test-output/<domain>/<layer>/.../*_chunks/.../data.jsonl
task bench:chunks:regen

# 2. Sample 200 diverse, extraction-aware GT candidates.
#    Writes .test-output/gt-candidates.json
task bench:gt-candidates

# 3. Run extraction (NER+SPO) for every configured model.
#    Per-model JSON fixtures land in runs/<run-id>/extractions/.
task bench:run

# 4. Generate ensemble consensus GT — restricted to the 200 candidates
#    automatically when gt-candidates.json is present.
task bench:ground-truth

# 5. Score per-domain F1 + render the report (viewer-ui at task bench:view).
task bench:report
```

Each step is independently re-runnable and idempotent given the upstream
output. Cache layout under `.test-output/` keeps re-invocations fast.

---

## What this script does NOT do

- It does not annotate anything. Human review (via the viewer-ui GT
  editor against `ground-truth/active.json`) is unchanged.
- It does not modify `BenchmarkStore`. The candidate file is purely
  advisory; the GT generation flow consumes it.
- It does not run the full ensemble. The optional `--score-extractions`
  pass uses a single cheap NER model (GLiNER) for filtering — not the
  multi-model NER+SPO ensemble that `task bench:ground-truth` runs.

---

## Tickets

- **CD-erc3** [P1] — scale GT to 200+ chunks (this script is the
  scaffold; blocked on more upstream content)
- **CD-s4em** [P2] — expand `bill_manifest.yaml` from 1 to ~30 bills
  (raises congress pool from 4 → ~250)
- **CD-oa7k** [P2] — regenerate audio cache for the other 6 manifest
  videos (raises media pool from 28 → ~200)
- **CD-991f** [P3] — viewer-ui GT editor batch-annotation UX
- **CD-4ssa** [P3] — Taskfile `bench:gt-candidates` target +
  `--candidates` plumbing through `generate_ensemble_ground_truth`
- **CD-foy3** [P2] — training-data generator (GT + extractions →
  SFT/DPO JSONL); downstream of CD-erc3

Until CD-s4em + CD-oa7k ship, the practical pool ceiling is `28 + 4 +
5000 = 5032` chunks, of which the sampler picks 200. The sample is
deterministically diverse within that ceiling but won't reach genuine
200-chunk SFT scale until those upstream tickets land.

---

## Ground Truth Format — v2 (doc-anchored, Phase 0 CD-9wno)

**Status**: shipped as of Phase 0. Existing GT files must be migrated via
`scripts/migrate_gt_to_doc_anchored.py` before v3 topology work begins.

### Why

The old GT format anchored each entry to a `chunk_id`.  When the chunker
changes (v3 rewrite, CD-rwlq), every existing GT entry is orphaned — the
new chunker produces different `chunk_id` values for the same doc content.

The new format anchors to absolute document character positions
(`doc_char_start` / `doc_char_end`), so GT survives any chunker change as
long as the source document bytes are stable.

### New GT entry shape

```json
{
  "doc_id": "joe-rogan-2284",
  "doc_char_start": 18432,
  "doc_char_end": 19280,
  "text_excerpt": "...human-readable excerpt, not the join key...",
  "legacy_chunk_id": "joe-rogan-2284:chunk-7",
  "mentions": [
    {
      "text": "Alex Jones",
      "mention_type": "PERSON",
      "doc_char_start": 18450,
      "doc_char_end": 18460,
      "confidence": 0.95
    }
  ],
  "propositions": [...],
  "reviewed": null
}
```

The **join key** is `(doc_id, IntervalTree of [doc_char_start, doc_char_end))`.
`legacy_chunk_id` is diagnostic only and not a scoring key.

### Scoring join (new)

At score time, for each model-output chunk:

1. Look up `gt_index[chunk.document_id]` — an `IntervalTree`.
2. Query `[chunk_char_offset, chunk_char_offset + len(chunk.text))`.
3. For each overlapping GT entry, subtract `chunk_char_offset` from each
   mention's `doc_char_start/end` to get chunk-relative spans, then score.
4. Edge case: GT entry overlaps multiple chunks (overlap region) — score
   against the chunk with the largest overlap; log duplicates at DEBUG.

See `tests/shared/gt_translation.py` for the helper implementations and
`tests/test_gt_scoring_invariant.py` for the invariant test.

### Migration script

```bash
# Dry run — preview what would change
python scripts/migrate_gt_to_doc_anchored.py --dry-run

# Migrate all GT files (MinIO/Tilt must be running)
python scripts/migrate_gt_to_doc_anchored.py
```

Originals are backed up to
`s3://dagster/bench/ground-truth/_backup_pre_v3/<name>.json` before
overwriting.  The script is idempotent — files already in the new format
are skipped.

### Viewer-UI behaviour

The SPA editor works in chunk-relative coordinates (unchanged).  The bench
API (`packages/media-ingest/src/media_ingest/viewer/routes/bench.py`):

- **GET** `/viewer/api/bench/ground-truth/<name>.json` — translates
  doc-frame spans to chunk-relative `span_start/span_end` on the fly.
- **PUT** `/viewer/api/bench/ground-truth/<name>.json` — translates
  incoming chunk-relative spans back to doc-frame before persisting.

Both paths degrade gracefully when chunk metadata is unavailable.
