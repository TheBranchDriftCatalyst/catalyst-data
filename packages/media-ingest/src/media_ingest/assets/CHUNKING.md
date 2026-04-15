# Chunking Strategy

Speaker-aware chunking that preserves natural speech boundaries.

## Pipeline

```
media_diarization (GPU)
  → media_segment_merge (CPU) — collapses same-speaker segments within 7s gap
    → media_chunks (CPU) — creates chunks for embedding + LLM extraction
```

## Three Strategies

### 1. `speaker_turn` — whole turns kept intact

When a merged speaker turn is **under 1500 characters**, it becomes a single chunk.
Most conversational exchanges (questions, short answers) fit here.

- **Provenance**: exact `start_s`/`end_s` from the segment
- **When**: short-to-medium turns (typical interview back-and-forth)

### 2. `speech_pause_split` — split at natural pauses

When a turn exceeds 1500 chars, scan the **word-level timestamps** for gaps
**>= 1.0 seconds** between consecutive words. Split at those pause points.

These pauses correspond to natural breath breaks, topic transitions, or
rhetorical pauses in speech — semantically coherent break points.

- **Provenance**: exact `start_s`/`end_s` from the word timestamps at each split boundary
- **When**: long monologues with natural pauses (most common for extended answers)
- **Tunable**: `PAUSE_THRESHOLD_S` env var (default 1.0s)

### 3. `text_split_fallback` — last resort text splitter

If after pause splitting, a sub-chunk is **still over 1500 chars** (speaker
talked for minutes without a single 1s pause), fall back to
`RecursiveCharacterTextSplitter` with 800-char chunks, zero overlap.

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

## Tuning

Run `python scripts/chunk_stats.py` against test fixtures to see:
- Segment merge collapse ratio
- Pause distribution (gap lengths between words)
- Chunk size distribution
- Speaker time breakdown
- Timestamp coverage

Key parameters:
- `MAX_CHUNK_CHARS = 1500` — threshold for "oversized" turns
- `PAUSE_THRESHOLD_S = 1.0` — minimum pause to split on
- `gap_threshold_s = 7.0` — segment merge gap (in `media_segment_merge`)
