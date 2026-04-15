#!/usr/bin/env python3
"""Chunk pipeline stats — analyze diarization, merging, and chunking quality.

Usage:
    python scripts/chunk_stats.py                           # uses test fixtures
    python scripts/chunk_stats.py --fixtures tests/fixtures # explicit path
"""

import argparse
import json
from pathlib import Path


def load(fixtures: Path, name: str) -> dict | list:
    f = fixtures / f"{name}.json"
    if not f.exists():
        print(f"  [missing] {f}")
        return {}
    return json.loads(f.read_text())


def fmt_time(s: float) -> str:
    m, sec = divmod(s, 60)
    return f"{int(m)}:{sec:05.2f}"


def main():
    parser = argparse.ArgumentParser(description="Chunk pipeline stats")
    parser.add_argument("--fixtures", type=Path, default=Path("tests/fixtures"))
    args = parser.parse_args()

    transcription = load(args.fixtures, "transcription")
    diarization = load(args.fixtures, "diarization")
    merge = load(args.fixtures, "segment_merge")
    chunks = load(args.fixtures, "chunks")

    if not transcription:
        print("No fixtures found. Run: pytest tests/test_pipeline_integration.py -v -s")
        return

    duration = transcription.get("duration_s", 0)

    # ── Transcription ────────────────────────────────────────────────
    t_segs = transcription.get("segments", [])
    print("=" * 70)
    print("TRANSCRIPTION")
    print("=" * 70)
    print(f"  Duration:     {fmt_time(duration)} ({duration:.0f}s)")
    print(f"  Language:     {transcription.get('language')} ({transcription.get('language_probability', 0):.0%})")
    print(f"  Segments:     {len(t_segs)}")
    if t_segs:
        seg_lens = [s["end"] - s["start"] for s in t_segs]
        print(
            f"  Seg duration: min={min(seg_lens):.1f}s  avg={sum(seg_lens) / len(seg_lens):.1f}s  max={max(seg_lens):.1f}s"
        )
        words_per_seg = [len(s.get("words", [])) for s in t_segs]
        print(
            f"  Words/seg:    min={min(words_per_seg)}  avg={sum(words_per_seg) / len(words_per_seg):.0f}  max={max(words_per_seg)}"
        )
        total_words = sum(words_per_seg)
        print(f"  Total words:  {total_words}")

    # ── Diarization ──────────────────────────────────────────────────
    if diarization:
        d_segs = diarization.get("segments", [])
        speakers = diarization.get("speakers", [])
        print()
        print("=" * 70)
        print("DIARIZATION")
        print("=" * 70)
        print(f"  Speakers:     {len(speakers)} — {', '.join(speakers)}")
        print(f"  Device:       {diarization.get('diarization_device', '?')}")
        print(f"  Time:         {diarization.get('diarization_time_s', 0):.1f}s")
        if duration > 0:
            rtf = duration / max(diarization.get("diarization_time_s", 1), 0.1)
            print(f"  Realtime:     {rtf:.1f}x")

        # Speaker distribution
        speaker_time: dict[str, float] = {}
        speaker_segs: dict[str, int] = {}
        for s in d_segs:
            spk = s.get("speaker", "UNKNOWN")
            dur = s["end"] - s["start"]
            speaker_time[spk] = speaker_time.get(spk, 0) + dur
            speaker_segs[spk] = speaker_segs.get(spk, 0) + 1

        print(f"\n  {'Speaker':<15} {'Time':>8} {'Pct':>6} {'Segs':>6}")
        print(f"  {'-' * 15} {'-' * 8} {'-' * 6} {'-' * 6}")
        for spk in sorted(speaker_time, key=speaker_time.get, reverse=True):
            t = speaker_time[spk]
            pct = t / duration * 100 if duration > 0 else 0
            print(f"  {spk:<15} {fmt_time(t):>8} {pct:5.1f}% {speaker_segs[spk]:>6}")

        # Pause analysis (gaps between consecutive segments)
        pauses = []
        for i in range(1, len(d_segs)):
            gap = d_segs[i]["start"] - d_segs[i - 1]["end"]
            if gap > 0:
                same_speaker = d_segs[i].get("speaker") == d_segs[i - 1].get("speaker")
                pauses.append((gap, same_speaker, d_segs[i - 1].get("speaker", "?")))

        if pauses:
            gaps = [p[0] for p in pauses]
            same_spk_gaps = [p[0] for p in pauses if p[1]]
            diff_spk_gaps = [p[0] for p in pauses if not p[1]]
            print(f"\n  Pauses:       {len(pauses)} total")
            print(f"  All gaps:     min={min(gaps):.2f}s  avg={sum(gaps) / len(gaps):.2f}s  max={max(gaps):.2f}s")
            if same_spk_gaps:
                print(f"  Same-speaker: {len(same_spk_gaps)} gaps, avg={sum(same_spk_gaps) / len(same_spk_gaps):.2f}s")
            if diff_spk_gaps:
                print(f"  Speaker swap: {len(diff_spk_gaps)} gaps, avg={sum(diff_spk_gaps) / len(diff_spk_gaps):.2f}s")

    # ── Segment Merge ────────────────────────────────────────────────
    if merge:
        pre = merge.get("pre_merge_segments", 0)
        post = merge.get("post_merge_segments", 0)
        m_segs = merge.get("segments", [])
        print()
        print("=" * 70)
        print("SEGMENT MERGE")
        print("=" * 70)
        print(f"  Pre-merge:    {pre} segments")
        print(f"  Post-merge:   {post} segments")
        print(f"  Collapsed:    {pre - post} ({(pre - post) / max(pre, 1) * 100:.0f}%)")

        if m_segs:
            turn_lens = [len(s.get("text", "")) for s in m_segs]
            turn_durs = [s["end"] - s["start"] for s in m_segs]
            word_counts = [len(s.get("words", [])) for s in m_segs]
            print(f"\n  {'Turn':<6} {'Speaker':<15} {'Start':>8} {'End':>8} {'Dur':>6} {'Chars':>6} {'Words':>6}")
            print(f"  {'-' * 6} {'-' * 15} {'-' * 8} {'-' * 8} {'-' * 6} {'-' * 6} {'-' * 6}")
            for i, s in enumerate(m_segs):
                spk = s.get("speaker", "?")
                dur = s["end"] - s["start"]
                chars = len(s.get("text", ""))
                words = len(s.get("words", []))
                print(
                    f"  {i:<6} {spk:<15} {fmt_time(s['start']):>8} {fmt_time(s['end']):>8} {dur:5.0f}s {chars:>6} {words:>6}"
                )

            print(
                f"\n  Turn chars:   min={min(turn_lens)}  avg={sum(turn_lens) / len(turn_lens):.0f}  max={max(turn_lens)}"
            )
            print(
                f"  Turn dur:     min={min(turn_durs):.0f}s  avg={sum(turn_durs) / len(turn_durs):.0f}s  max={max(turn_durs):.0f}s"
            )
            print(
                f"  Turn words:   min={min(word_counts)}  avg={sum(word_counts) / len(word_counts):.0f}  max={max(word_counts)}"
            )

    # ── Chunks ───────────────────────────────────────────────────────
    if chunks and isinstance(chunks, list):
        print()
        print("=" * 70)
        print("CHUNKS")
        print("=" * 70)
        whole = [c for c in chunks if c.get("metadata", {}).get("strategy") == "speaker_turn"]
        split = [c for c in chunks if c.get("metadata", {}).get("strategy") == "speech_pause_split"]
        print(f"  Total:        {len(chunks)}")
        print(f"  Whole turns:  {len(whole)}")
        print(f"  Split chunks: {len(split)}")

        char_lens = [len(c.get("text", "")) for c in chunks]
        print(f"  Chars:        min={min(char_lens)}  avg={sum(char_lens) / len(char_lens):.0f}  max={max(char_lens)}")

        # Timestamp coverage
        starts = [c["metadata"]["start_s"] for c in chunks if "start_s" in c.get("metadata", {})]
        ends = [c["metadata"]["end_s"] for c in chunks if "end_s" in c.get("metadata", {})]
        if starts and ends:
            print(f"  Time range:   {fmt_time(min(starts))} → {fmt_time(max(ends))}")
            coverage = max(ends) - min(starts)
            print(f"  Coverage:     {coverage:.0f}s / {duration:.0f}s ({coverage / max(duration, 1) * 100:.0f}%)")

        # Speaker distribution in chunks
        spk_chunks: dict[str, int] = {}
        for c in chunks:
            spk = c.get("metadata", {}).get("speaker", "?")
            spk_chunks[spk] = spk_chunks.get(spk, 0) + 1
        print(f"\n  {'Speaker':<15} {'Chunks':>8}")
        print(f"  {'-' * 15} {'-' * 8}")
        for spk, cnt in sorted(spk_chunks.items(), key=lambda x: -x[1]):
            print(f"  {spk:<15} {cnt:>8}")

    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
