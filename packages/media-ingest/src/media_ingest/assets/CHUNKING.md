# Speaker-Aware Chunking

Speaker-aware chunking that preserves natural speech boundaries. The strategy
itself lives on the shared `ChunkingResource` in `dagster_io.chunking` so the
Dagster UI launchpad `chunk_size` setting controls audio chunking the same
way it controls text chunking. The `media_chunks` Dagster asset is a thin
wrapper that calls `chunking.chunk_speaker_segments(...)`.

## Pipeline

```
media_diarization (Metal/CUDA/CPU)
  → media_segment_merge (CPU) — collapses same-speaker segments within 7s gap
    → media_chunks (CPU) — calls ChunkingResource.chunk_speaker_segments
```

## Three Strategies

### 1. `speaker_turn` — whole turns kept intact

When a merged speaker turn is **under `max_chars`** (default = `chunking.chunk_size`),
it becomes a single chunk. Most conversational exchanges (questions, short
answers) fit here.

- **Provenance**: exact `start_s`/`end_s` from the segment
- **When**: short-to-medium turns (typical interview back-and-forth)

### 2. `speech_pause_split` — split at natural pauses

When a turn exceeds `max_chars`, scan the **word-level timestamps** for gaps
**>= `pause_threshold_s`** (default 1.0s) between consecutive words. Split
at those pause points.

These pauses correspond to natural breath breaks, topic transitions, or
rhetorical pauses in speech — semantically coherent break points.

- **Provenance**: exact `start_s`/`end_s` from the word timestamps at each split boundary
- **When**: long monologues with natural pauses (most common for extended answers)

### 3. `text_split_fallback` — last resort text splitter

If after pause splitting a sub-chunk is **still over `max_chars`** (speaker
talked for minutes without a single 1s pause), fall back to
`RecursiveCharacterTextSplitter` with `fallback_chunk_size = max_chars // 2`,
zero overlap.

- **Provenance**: proportional `start_s`/`end_s` (estimated from position in the text)
- **When**: rare — only for extremely fast continuous speech with no natural pauses

## Chunk Metadata

Every chunk carries:

| Field | Description |
|-------|-------------|
| `speaker` | Speaker label (e.g., `SPEAKER_00`) |
| `start_s` | Start time in seconds (exact for strategies 1-2, proportional for 3) |
| `end_s` | End time in seconds |
| `strategy` | Which strategy produced this chunk |
| `source` | Always `media_ingest` |
| `language` | Detected language |

Plus the standard `TextChunk` fields: `chunk_id`, `document_id`, `text`,
`index`, `total_chunks`, `content_hash`.

## Tuning

Resource-level (controls all three domain chunkers via Dagster UI launchpad):

- `ChunkingResource.chunk_size` — default oversize threshold (env `CHUNK_SIZE`, default 1000)
- `ChunkingResource.chunk_overlap` — default overlap (env `CHUNK_OVERLAP`, default 200)
- `ChunkingResource.prepend_title` — whether to prepend the document title to each chunk (default True)

Audio-specific (passed to `chunk_speaker_segments`):

- `max_chars` — override `chunk_size` for this call
- `pause_threshold_s` — minimum word-gap to split at (default 1.0s)
- `fallback_chunk_size` — tier-3 splitter chunk size (default `max_chars // 2`)
- `gap_threshold_s` — segment merge gap (in `media_segment_merge`, currently 7.0s)

Run `python scripts/chunk_stats.py` against test fixtures to see:

- Segment merge collapse ratio
- Pause distribution (gap lengths between words)
- Chunk size distribution
- Speaker time breakdown
- Timestamp coverage

## Adding a new audio chunking strategy

The audio chunker lives on `ChunkingResource` so future variants can be added
as sibling methods without touching the asset signature:

```python
# in libs/dagster-io/src/dagster_io/chunking.py
class ChunkingResource(ConfigurableResource):
    def chunk_vad_windows(self, segments, ...) -> list[TextChunk]: ...
    def chunk_sentence_boundary(self, segments, ...) -> list[TextChunk]: ...
```

Then `media_chunks` picks one:

```python
chunks = chunking.chunk_vad_windows(segments, doc_id, title, ...)
```

## Testing

- **Unit**: `pytest packages/media-ingest/tests/test_speaker_chunks.py` — 12 cases
  covering speaker-turn, pause-splitting, mixed strategies. Tests pass
  `max_chars=1500` explicitly so they're deterministic regardless of
  `CHUNK_SIZE` env.
- **Integration**: `pytest tests/test_pipeline_integration.py::test_chunks_produced`
  — runs the chunker against real cached transcription/diarization output.
  Same code path the production `media_chunks` asset runs.
