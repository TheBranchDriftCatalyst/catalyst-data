#!/usr/bin/env python3
"""QA test: Verify faster-whisper transcription + pyannote diarization end-to-end.

Run standalone (no dagster required):
    python tests/test_transcription_qa.py [--audio /path/to/file.mp3] [--output-dir .output]
"""

import argparse
import json
import os
import subprocess
import tempfile
import time


def _patch_hf_hub_auth():
    """Monkey-patch huggingface_hub so use_auth_token→token everywhere.

    pyannote.audio 3.4 passes use_auth_token= to multiple hf_hub functions,
    but huggingface_hub >=1.0 removed that kwarg. We patch at the module
    level so every import picks it up.
    """
    import functools

    import huggingface_hub
    import huggingface_hub.file_download as _fd

    def _wrap(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if "use_auth_token" in kw:
                kw.setdefault("token", kw.pop("use_auth_token"))
            return fn(*a, **kw)

        return wrapper

    for target in ("hf_hub_download", "cached_download"):
        for mod in (huggingface_hub, _fd):
            orig = getattr(mod, target, None)
            if orig and not getattr(orig, "_patched", False):
                patched = _wrap(orig)
                patched._patched = True
                setattr(mod, target, patched)

    # Patch any already-imported pyannote modules
    import sys

    for name, mod in sys.modules.items():
        if "pyannote" in name and mod is not None:
            for attr in ("hf_hub_download", "cached_download"):
                fn = getattr(mod, attr, None)
                if fn and not getattr(fn, "_patched", False):
                    setattr(mod, attr, _wrap(fn))


def download_sample_audio(dest):
    """Download a short multi-speaker audio sample for testing."""
    print("[setup] Generating multi-speaker test audio with ffmpeg...")
    urls = [
        "https://www.voiptroubleshooter.com/open_speech/american/OSR_us_000_0010_8k.wav",
        "https://www.voiptroubleshooter.com/open_speech/american/OSR_us_000_0061_8k.wav",
    ]
    parts = []
    for i, url in enumerate(urls):
        part = os.path.join(dest, f"speaker_{i}.wav")
        subprocess.run(["wget", "-q", "-O", part, url], check=True)
        parts.append(part)

    output = os.path.join(dest, "multi_speaker_test.wav")
    filter_complex = "[0:a]aresample=16000[a0];[1:a]aresample=16000[a1];[a0][a1]concat=n=2:v=0:a=1[out]"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            parts[0],
            "-i",
            parts[1],
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            output,
        ],
        check=True,
        capture_output=True,
    )
    print(f"[setup] Created test audio: {output}")
    return output


def save_json(data, path):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"[saved] {path}")


def test_whisper_only(audio_path, model_name="base"):
    """Test 1: faster-whisper transcription without diarization."""
    from faster_whisper import WhisperModel

    print(f"\n{'=' * 60}")
    print(f"TEST 1: faster-whisper transcription (model={model_name})")
    print(f"{'=' * 60}")

    start = time.monotonic()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    load_time = time.monotonic() - start
    print(f"[model] Loaded in {load_time:.1f}s")

    start = time.monotonic()
    segments, info = model.transcribe(audio_path, word_timestamps=True)
    segments_list = list(segments)
    transcribe_time = time.monotonic() - start

    print(f"[info] Language: {info.language} (prob={info.language_probability:.2f})")
    print(f"[info] Duration: {info.duration:.1f}s")
    print(f"[info] Transcription time: {transcribe_time:.1f}s")
    print(f"[info] Segments: {len(segments_list)}")
    print("\n[transcript]")
    for s in segments_list:
        print(f"  [{s.start:.1f}s - {s.end:.1f}s] {s.text.strip()}")

    result = {
        "model": model_name,
        "language": info.language,
        "language_probability": info.language_probability,
        "duration_s": info.duration,
        "transcription_time_s": round(transcribe_time, 1),
        "segment_count": len(segments_list),
        "full_text": " ".join(s.text.strip() for s in segments_list),
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text.strip(),
                "words": [
                    {
                        "word": w.word,
                        "start": w.start,
                        "end": w.end,
                        "probability": round(w.probability, 3),
                    }
                    for w in (s.words or [])
                ],
            }
            for s in segments_list
        ],
    }

    return segments_list, info, result


def test_diarization(audio_path, hf_token):
    """Test 2: pyannote speaker diarization."""
    from pyannote.audio import Pipeline

    print(f"\n{'=' * 60}")
    print("TEST 2: pyannote speaker diarization")
    print(f"{'=' * 60}")

    _patch_hf_hub_auth()

    # torch 2.6+ defaults weights_only=True but pyannote checkpoints
    # contain custom types. lightning_fabric passes weights_only=True
    # explicitly, so we must override it, not just set a default.
    import torch

    _orig_load = torch.load

    def _patched_load(*a, **kw):
        kw["weights_only"] = False
        return _orig_load(*a, **kw)

    torch.load = _patched_load

    start = time.monotonic()
    os.environ["HF_TOKEN"] = hf_token
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )
    if pipeline is None:
        raise RuntimeError(
            "Failed to load pyannote pipeline. Accept the license at https://hf.co/pyannote/speaker-diarization-3.1"
        )
    load_time = time.monotonic() - start
    print(f"[model] Loaded in {load_time:.1f}s")

    start = time.monotonic()
    diarization = pipeline(audio_path)
    diarize_time = time.monotonic() - start

    speakers = set()
    turns = list(diarization.itertracks(yield_label=True))
    print(f"[info] Diarization time: {diarize_time:.1f}s")
    print(f"[info] Speaker turns: {len(turns)}")
    print("\n[turns]")
    for turn, _, speaker in turns:
        speakers.add(speaker)
        print(f"  [{turn.start:.1f}s - {turn.end:.1f}s] {speaker}")

    print(f"\n[info] Unique speakers: {sorted(speakers)}")

    result = {
        "diarization_time_s": round(diarize_time, 1),
        "speaker_count": len(speakers),
        "speakers": sorted(speakers),
        "turns": [
            {
                "start": round(turn.start, 2),
                "end": round(turn.end, 2),
                "speaker": speaker,
            }
            for turn, _, speaker in turns
        ],
    }

    return diarization, result


def test_aligned_output(segments_list, diarization):
    """Test 3: Align whisper segments with speaker turns."""
    print(f"\n{'=' * 60}")
    print("TEST 3: Speaker-aligned transcript")
    print(f"{'=' * 60}")

    speaker_turns = list(diarization.itertracks(yield_label=True))

    def find_speaker(ts):
        for turn, _, speaker in speaker_turns:
            if turn.start <= ts <= turn.end:
                return speaker
        return None

    aligned = []
    for s in segments_list:
        mid = (s.start + s.end) / 2
        speaker = find_speaker(mid)

        if s.words:
            word_speakers = []
            for w in s.words:
                wmid = (w.start + w.end) / 2
                word_speakers.append(find_speaker(wmid))
            valid = [sp for sp in word_speakers if sp]
            if valid:
                speaker = max(set(valid), key=valid.count)

        aligned.append(
            {
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": s.text.strip(),
                "speaker": speaker,
            }
        )

    # Print speaker-attributed transcript
    current_speaker = None
    speaker_transcript = ""
    for seg in aligned:
        if seg["speaker"] != current_speaker:
            current_speaker = seg["speaker"]
            label = f"\n[{current_speaker or 'UNKNOWN'}]:\n"
            print(label, end="")
            speaker_transcript += label
        line = f"  {seg['text']}\n"
        print(line, end="")
        speaker_transcript += line

    # Summary
    speakers = {s["speaker"] for s in aligned if s["speaker"]}
    print(f"\n[summary] {len(aligned)} segments, {len(speakers)} speakers: {sorted(speakers)}")

    result = {
        "segment_count": len(aligned),
        "speaker_count": len(speakers),
        "speakers": sorted(speakers),
        "speaker_transcript": speaker_transcript.strip(),
        "full_text": " ".join(s["text"] for s in aligned),
        "segments": aligned,
    }

    return result


def main():
    parser = argparse.ArgumentParser(description="QA test for transcription + diarization")
    parser.add_argument("--audio", help="Path to audio file")
    parser.add_argument("--model", default="base", help="Whisper model")
    parser.add_argument(
        "--hf-token",
        default=os.environ.get("HF_TOKEN", ""),
        help="HuggingFace token (or HF_TOKEN env var)",
    )
    parser.add_argument(
        "--skip-diarize",
        action="store_true",
        help="Skip diarization test",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write JSON results",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = args.audio
        if not audio_path:
            audio_path = download_sample_audio(tmpdir)

        print(f"\n[config] Audio: {audio_path}")
        print(f"[config] Model: {args.model}")
        print(f"[config] HF token: {'set' if args.hf_token else 'NOT SET'}")
        print(f"[config] Diarization: {'skip' if args.skip_diarize else 'enabled'}")
        if output_dir:
            print(f"[config] Output dir: {output_dir}")

        # Test 1: Whisper transcription
        segments_list, info, whisper_result = test_whisper_only(audio_path, args.model)
        if output_dir:
            save_json(whisper_result, os.path.join(output_dir, "whisper.json"))

        if not args.skip_diarize:
            if not args.hf_token:
                print("\n[WARN] No HF_TOKEN — skipping diarization")
                return

            # Test 2: Diarization
            diarization, diarize_result = test_diarization(audio_path, args.hf_token)
            if output_dir:
                save_json(
                    diarize_result,
                    os.path.join(output_dir, "diarization.json"),
                )

            # Test 3: Aligned output
            aligned_result = test_aligned_output(segments_list, diarization)
            if output_dir:
                save_json(
                    aligned_result,
                    os.path.join(output_dir, "aligned.json"),
                )
                # Also write a plain-text speaker transcript
                txt_path = os.path.join(output_dir, "transcript.txt")
                with open(txt_path, "w") as f:
                    f.write(aligned_result["speaker_transcript"])
                print(f"[saved] {txt_path}")

    print(f"\n{'=' * 60}")
    print("QA COMPLETE")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
