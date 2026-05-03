"""Text chunking utilities for RAG pipelines.

Uses LangChain's RecursiveCharacterTextSplitter under the hood — the industry
standard for chunk boundary selection.  Exposes a shared TextChunk model and
a ChunkingResource (ConfigurableResource) that surfaces chunk parameters in
the Dagster UI launchpad.

Supports per-document-type overrides so assets can route different content
types to optimal chunk sizes (e.g. short metadata docs stay atomic, dense
legal text gets larger chunks).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

from dagster import ConfigurableResource
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field

from dagster_io.logging import get_logger
from dagster_io.metrics import CHUNK_PROCESSING_DURATION, CHUNKS_CREATED, track_duration
from dagster_io.text import normalize_text

logger = get_logger(__name__)

DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


# ---------------------------------------------------------------------------
# Speaker-segment chunking helpers (used by ChunkingResource.chunk_speaker_segments)
# ---------------------------------------------------------------------------


@dataclass
class _SubSegment:
    """A slice of a speaker turn with text and precise timestamps."""

    text: str
    start: float
    end: float
    strategy: str


def _speaker_sub_segment_from_words(words: list[dict]) -> _SubSegment:
    """Build a _SubSegment from a contiguous word slice."""
    return _SubSegment(
        text="".join(w.get("word", "") for w in words).strip(),
        start=words[0].get("start", 0),
        end=words[-1].get("end", 0),
        strategy="speech_pause_split",
    )


def _speaker_split_on_pauses(words: list[dict], text: str, threshold: float = 1.0) -> list[_SubSegment]:
    """Split a word sequence at natural speech pauses (gaps >= threshold).

    Returns _SubSegments with exact word-level timestamps. Falls back to the
    full text as a single _SubSegment when the input has no qualifying pauses.
    """
    if not words:
        return [_SubSegment(text=text, start=0, end=0, strategy="speech_pause_split")]

    split_at = [i for i in range(1, len(words)) if words[i].get("start", 0) - words[i - 1].get("end", 0) >= threshold]
    if not split_at:
        return [_speaker_sub_segment_from_words(words)]

    boundaries = [0, *split_at, len(words)]
    return [
        _speaker_sub_segment_from_words(words[boundaries[i] : boundaries[i + 1]])
        for i in range(len(boundaries) - 1)
        if words[boundaries[i] : boundaries[i + 1]]
    ]


# ---------------------------------------------------------------------------
# Context-aware chunk sizing
# ---------------------------------------------------------------------------


@dataclass
class ChunkConfig:
    """Model-aware chunk sizing configuration.

    Computes a target chunk size from the model's context window minus
    prompt overhead and output reserve, then scales by ``context_fraction``.
    """

    model_context_tokens: int = 4096
    prompt_overhead_tokens: int = 1500  # system prompt + schema + few-shot
    output_reserve_tokens: int = 2000  # max expected extraction output
    context_fraction: float = 0.25  # use 25% of remaining context
    min_chunk_tokens: int = 100  # floor
    max_chunk_tokens: int = 8000  # ceiling (even for 128K models)
    overlap_tokens: int = 50
    strategy: str = "recursive"  # recursive|section|speaker|passthrough

    @property
    def target_tokens(self) -> int:
        available = self.model_context_tokens - self.prompt_overhead_tokens - self.output_reserve_tokens
        target = int(available * self.context_fraction)
        return max(self.min_chunk_tokens, min(target, self.max_chunk_tokens))

    @property
    def target_chars(self) -> int:
        return self.target_tokens * 4  # conservative estimate, refined by TokenCounter


class TokenCounter:
    """Count tokens using tiktoken when available, otherwise fall back to char/4."""

    def __init__(self, model: str = "gpt-4o"):
        self._encoder = None
        try:
            import tiktoken

            self._encoder = tiktoken.encoding_for_model(model)
        except (ImportError, KeyError):
            pass

    def count(self, text: str) -> int:
        if self._encoder:
            return len(self._encoder.encode(text))
        return len(text) // 4

    def chars_for_tokens(self, n_tokens: int) -> int:
        return n_tokens * 4  # approximate for char budget


PRESET_CONFIGS: dict[str, ChunkConfig] = {
    "small-local": ChunkConfig(model_context_tokens=4096),
    "medium-local": ChunkConfig(model_context_tokens=8192),
    "large-local": ChunkConfig(model_context_tokens=32768),
    "cloud-openai": ChunkConfig(model_context_tokens=128000),
    "cloud-anthropic": ChunkConfig(model_context_tokens=200000),
    "encoder": ChunkConfig(model_context_tokens=2048, context_fraction=0.5, max_chunk_tokens=512),
}


class TextChunk(BaseModel):
    """A chunk of text derived from a parent document."""

    chunk_id: str = Field(description="Unique chunk identifier (doc_id + index)")
    document_id: str = Field(description="Parent document ID")
    text: str = Field(description="Chunk text content")
    index: int = Field(description="Position within the parent document (0-based)")
    total_chunks: int = Field(description="Total chunks produced from parent document")
    metadata: dict = Field(default_factory=dict, description="Inherited + chunk-specific metadata")
    content_hash: str = Field(default="", description="SHA-256 of chunk text for dedup")

    def model_post_init(self, __context) -> None:
        if not self.content_hash and self.text:
            self.content_hash = hashlib.sha256(self.text.encode()).hexdigest()


class ChunkingResource(ConfigurableResource):
    """Configurable text chunking resource.

    All parameters are editable in the Dagster UI launchpad.  These serve as
    defaults; individual calls to ``chunk_document`` can override size/overlap
    for per-document-type optimization.

    Usage in assets::

        @asset
        def my_chunks(chunking: ChunkingResource, docs: list[Document]):
            # Use resource defaults
            chunks = chunking.chunk_document(doc_id, title, content)
            # Override for a specific doc type
            chunks = chunking.chunk_document(doc_id, title, content, chunk_size=2000)
            # Passthrough for short metadata docs
            chunks = chunking.passthrough(doc_id, title, content)
    """

    chunk_size: int = 1000
    chunk_overlap: int = 200
    prepend_title: bool = True

    def _splitter(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size or self.chunk_size,
            chunk_overlap=chunk_overlap or self.chunk_overlap,
            separators=DEFAULT_SEPARATORS,
            length_function=len,
        )

    def split_text(
        self,
        text: str,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> list[str]:
        """Split raw text into overlapping chunks."""
        if not text or not text.strip():
            return []
        return self._splitter(chunk_size, chunk_overlap).split_text(text)

    def chunk_document(
        self,
        document_id: str,
        title: str,
        content: str,
        metadata: dict | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> list[TextChunk]:
        """Chunk a document into TextChunk objects.

        Args:
            document_id: Parent document ID.
            title: Document title (prepended to each chunk if prepend_title=True).
            content: Full text content to split.
            metadata: Extra metadata to attach to each chunk.
            chunk_size: Override the resource default for this call.
            chunk_overlap: Override the resource default for this call.
        """
        # Normalize at ingestion — NFKC converts fullwidth chars, strips control chars
        title = normalize_text(title) if title else title
        content = normalize_text(content) if content else content

        size = chunk_size or self.chunk_size
        overlap = chunk_overlap or self.chunk_overlap
        logger.debug(
            "Chunking document=%s size=%d overlap=%d content_len=%d",
            document_id,
            size,
            overlap,
            len(content),
        )
        with track_duration(CHUNK_PROCESSING_DURATION, {"strategy": "recursive"}):
            raw_chunks = self.split_text(content, chunk_size=size, chunk_overlap=overlap)

        if not raw_chunks:
            return []

        total = len(raw_chunks)
        CHUNKS_CREATED.labels(strategy="recursive").inc(total)
        logger.info(
            "Chunked document=%s into %d chunks (size=%d, overlap=%d)",
            document_id,
            total,
            size,
            overlap,
        )
        base_meta = {
            **(metadata or {}),
            "chunk_size": size,
            "chunk_overlap": overlap,
            "strategy": "recursive",
        }

        # Compute character offsets so spans can map back to the original document
        full_text = f"{title}\n\n{content}" if (self.prepend_title and title) else content
        chunks = []
        for i, text in enumerate(raw_chunks):
            char_offset = full_text.find(text)
            chunk_meta = {
                **base_meta,
                "chunk_char_offset": char_offset if char_offset >= 0 else None,
            }
            chunks.append(
                TextChunk(
                    chunk_id=f"{document_id}:chunk-{i}",
                    document_id=document_id,
                    text=f"{title}\n\n{text}" if (self.prepend_title and title) else text,
                    index=i,
                    total_chunks=total,
                    metadata=chunk_meta,
                )
            )
        return chunks

    def chunk_speaker_segments(
        self,
        segments: list[dict],
        document_id: str,
        title: str,
        metadata: dict | None = None,
        max_chars: int | None = None,
        pause_threshold_s: float = 1.0,
        fallback_chunk_size: int | None = None,
    ) -> list[TextChunk]:
        """Build TextChunks from speaker-attributed audio segments.

        Three-tier strategy:
          1. ``speaker_turn``       — whole turn fits in ``max_chars``
          2. ``speech_pause_split`` — oversize turn split at >= ``pause_threshold_s`` word gaps
          3. ``text_split_fallback`` — last-resort RecursiveCharacterTextSplitter

        This is the audio-domain counterpart to ``chunk_document``. It keeps
        speaker-turn boundaries intact whenever possible so downstream LLM
        extraction sees coherent conversational units. Future audio chunking
        strategies (VAD-window, sentence-boundary, etc.) can be added as
        sibling methods on this resource without changing the asset signature.

        Args:
            segments: dicts with ``{text, start, end, words[], speaker}``.
            document_id, title: passed through to each TextChunk.
            metadata: base metadata applied to every chunk; per-chunk
                ``speaker``, ``start_s``, ``end_s``, ``strategy`` are added on top.
            max_chars: oversize threshold; defaults to ``self.chunk_size`` so the
                Dagster UI launchpad ``chunk_size`` setting controls audio chunking
                the same way it controls text chunking.
            pause_threshold_s: minimum word-gap (seconds) to split at in tier 2.
            fallback_chunk_size: chunk_size for tier 3 splitter; defaults to
                ``max_chars // 2``.
        """
        limit = max_chars if max_chars is not None else self.chunk_size
        fallback = fallback_chunk_size or (limit // 2) or 800
        base_meta = {**(metadata or {})}

        chunks: list[TextChunk] = []
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue

            speaker = seg.get("speaker", "UNKNOWN")
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            words = seg.get("words", [])

            if len(text) <= limit:
                sub_segs: list[_SubSegment] = [
                    _SubSegment(text=text, start=seg_start, end=seg_end, strategy="speaker_turn")
                ]
            else:
                sub_segs = []
                for sub in _speaker_split_on_pauses(words, text, threshold=pause_threshold_s):
                    if not sub.text:
                        continue
                    if len(sub.text) <= limit:
                        sub_segs.append(sub)
                    else:
                        pieces = self.split_text(sub.text, chunk_size=fallback, chunk_overlap=0)
                        n = max(len(pieces), 1)
                        duration = sub.end - sub.start
                        for i, piece in enumerate(pieces):
                            sub_segs.append(
                                _SubSegment(
                                    text=piece,
                                    start=sub.start + duration * (i / n),
                                    end=sub.start + duration * ((i + 1) / n),
                                    strategy="text_split_fallback",
                                )
                            )

            for sub in sub_segs:
                full_text = f"{title}\n\n{sub.text}" if (self.prepend_title and title) else sub.text
                chunks.append(
                    TextChunk(
                        chunk_id=f"{document_id}:chunk-{len(chunks)}",
                        document_id=document_id,
                        text=full_text,
                        index=len(chunks),
                        total_chunks=0,
                        metadata={
                            **base_meta,
                            "speaker": speaker,
                            "start_s": sub.start,
                            "end_s": sub.end,
                            "strategy": sub.strategy,
                        },
                    )
                )

        # Backfill total_chunks + recompute content_hash on the (now-final) text
        for c in chunks:
            c.total_chunks = len(chunks)
            c.content_hash = hashlib.sha256(c.text.encode()).hexdigest()

        if chunks:
            for strat, count in Counter(c.metadata.get("strategy") for c in chunks).items():
                CHUNKS_CREATED.labels(strategy=str(strat)).inc(count)

        return chunks

    def chunk_multi_speaker_segments(
        self,
        segments: list[dict],
        document_id: str,
        title: str,
        metadata: dict | None = None,
        target_chars: int | None = None,
        pause_threshold_s: float = 1.0,
        fallback_chunk_size: int | None = None,
        inline_speaker_tags: bool = True,
    ) -> list[TextChunk]:
        """Group consecutive speaker turns into multi-speaker windows of ~target_chars.

        Unlike ``chunk_speaker_segments`` (one chunk per speaker turn), this
        method packs N consecutive turns into one chunk until the target size
        is hit, then starts a new chunk. Inline speaker tags (``[SPEAKER_X] ``)
        preserve who-said-what for the LLM.

        Why: single-turn chunks (80–200 chars) starve the LLM of conversational
        context; coreference and SPO extraction both fail when each chunk is
        one disembodied utterance. Multi-speaker windowing keeps the natural
        conversation flow + gives the LLM enough surrounding context to do
        cross-turn entity resolution.

        When a *single* speaker turn exceeds target_chars, the method falls
        back to the speech-pause split logic from ``chunk_speaker_segments``
        for that one turn (so a 5-minute monologue gets broken into pause-
        aligned sub-chunks while still emitted as separate chunks).

        Args:
            segments: dicts with ``{text, start, end, words[], speaker}`` —
                same input shape as ``chunk_speaker_segments``.
            target_chars: target window size; defaults to ``self.chunk_size``.
            pause_threshold_s: word-gap threshold for splitting oversize single
                turns (forwarded to the single-turn fallback).
            fallback_chunk_size: tier-3 splitter size; defaults to
                ``target_chars // 2``.
            inline_speaker_tags: when True, prefix each turn's text with
                ``[SPEAKER_X] `` so the LLM sees who said what. Set False if
                you want bare concatenated text (e.g. for embedding-only flows).

        Output ``TextChunk`` metadata adds: ``speakers`` (list of all speakers
        in this chunk), ``primary_speaker`` (most-spoken-by-char-count),
        ``start_s``/``end_s`` (first turn start, last turn end), ``turn_count``,
        ``strategy`` ∈ {``multi_speaker_window``, ``multi_speaker_split``,
        ``text_split_fallback``}.
        """
        limit = target_chars if target_chars is not None else self.chunk_size
        fallback = fallback_chunk_size or (limit // 2) or 800
        base_meta = {**(metadata or {})}

        # Pre-format each turn's text (with inline speaker tag) so the buffer
        # accounting matches what the chunk will actually contain.
        def _format_turn(seg: dict) -> str:
            text = (seg.get("text") or "").strip()
            if not text:
                return ""
            if inline_speaker_tags:
                speaker = seg.get("speaker") or "UNKNOWN"
                return f"[{speaker}] {text}"
            return text

        chunks: list[TextChunk] = []

        def _emit(buffer_turns: list[dict], strategy: str = "multi_speaker_window") -> None:
            """Emit one chunk from the accumulated turns and reset the buffer."""
            if not buffer_turns:
                return
            text_parts = [_format_turn(t) for t in buffer_turns if (t.get("text") or "").strip()]
            if not text_parts:
                return
            chunk_text = "\n".join(text_parts)
            full_text = f"{title}\n\n{chunk_text}" if (self.prepend_title and title) else chunk_text

            speakers_in_chunk = sorted({t.get("speaker") or "UNKNOWN" for t in buffer_turns})
            speaker_chars: dict[str, int] = {}
            for t in buffer_turns:
                spk = t.get("speaker") or "UNKNOWN"
                speaker_chars[spk] = speaker_chars.get(spk, 0) + len((t.get("text") or "").strip())
            primary_speaker = max(speaker_chars, key=speaker_chars.get) if speaker_chars else "UNKNOWN"

            chunks.append(
                TextChunk(
                    chunk_id=f"{document_id}:chunk-{len(chunks)}",
                    document_id=document_id,
                    text=full_text,
                    index=len(chunks),
                    total_chunks=0,  # backfilled below
                    metadata={
                        **base_meta,
                        "speakers": speakers_in_chunk,
                        "primary_speaker": primary_speaker,
                        "speaker": primary_speaker,  # back-compat key for callers
                        "turn_count": len(buffer_turns),
                        "start_s": buffer_turns[0].get("start", 0),
                        "end_s": buffer_turns[-1].get("end", 0),
                        "strategy": strategy,
                    },
                )
            )

        buffer: list[dict] = []
        buffer_chars = 0

        for seg in segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue

            formatted = _format_turn(seg)
            seg_chars = len(formatted)

            # Single oversize turn — emit current buffer, then split this one
            # turn via speech-pause logic (preserve precise timestamps).
            if seg_chars > limit:
                _emit(buffer)
                buffer, buffer_chars = [], 0

                # Reuse single-speaker split logic for this oversize turn.
                # Build a one-segment chunks list, then re-tag the strategy.
                solo = self.chunk_speaker_segments(
                    [seg],
                    document_id=document_id,
                    title="",  # title already prepended at the multi-speaker level
                    metadata=base_meta,
                    max_chars=limit,
                    pause_threshold_s=pause_threshold_s,
                    fallback_chunk_size=fallback,
                )
                # Splice these in as multi_speaker_split chunks — preserve their
                # speaker_turn / speech_pause_split / text_split_fallback strategy
                # tags but mark them as part of the multi-speaker run.
                for s in solo:
                    s.chunk_id = f"{document_id}:chunk-{len(chunks)}"
                    s.index = len(chunks)
                    s.metadata = {
                        **base_meta,
                        **(s.metadata or {}),
                        "speakers": [s.metadata.get("speaker", "UNKNOWN")],
                        "primary_speaker": s.metadata.get("speaker", "UNKNOWN"),
                        "turn_count": 1,
                        "strategy": "multi_speaker_split",
                    }
                    chunks.append(s)
                continue

            # If adding this turn would push the buffer past the limit, flush first
            if buffer and buffer_chars + 1 + seg_chars > limit:
                _emit(buffer)
                buffer, buffer_chars = [], 0

            buffer.append(seg)
            buffer_chars += (1 if buffer_chars else 0) + seg_chars  # +1 for the joining newline

        if buffer:
            _emit(buffer)

        # Backfill total_chunks + recompute content_hash on final text
        for c in chunks:
            c.total_chunks = len(chunks)
            c.content_hash = hashlib.sha256(c.text.encode()).hexdigest()

        if chunks:
            for strat, count in Counter(c.metadata.get("strategy") for c in chunks).items():
                CHUNKS_CREATED.labels(strategy=str(strat)).inc(count)

        return chunks

    def refine_with_semantic(
        self,
        chunks: list[TextChunk],
        embedder,
        merge_threshold: float | None = None,
        max_chars_after_merge: int | None = None,
    ) -> list[TextChunk]:
        """Post-pass refinement: merge adjacent chunks with high semantic similarity.

        Use after ``chunk_multi_speaker_segments`` to collapse consecutive
        chunks that talk about the same topic — gives the LLM longer
        topically-coherent windows where the topic actually persists.

        Algorithm:
          1. Embed every chunk's text via ``embedder.embed(texts)``.
          2. Compute cosine similarity between each adjacent pair.
          3. If ``merge_threshold`` is not provided, derive it from the 75th
             percentile of pairwise similarities (the top-quartile of
             "this-flows-into-the-next" candidates).
          4. Walk pairs in order; when sim(i, i+1) >= threshold AND the
             merged size stays under ``max_chars_after_merge``, fuse them.

        We do NOT split low-similarity chunks here — splitting requires
        sub-chunk re-segmentation which is complex. Merging is the easier,
        higher-leverage win for conversational content.

        Args:
            chunks: output of ``chunk_multi_speaker_segments``.
            embedder: anything with ``embed(texts: list[str]) -> list[list[float]]``
                — e.g. ``EmbeddingResource.embed`` (bound method).
            merge_threshold: cosine similarity above which adjacent chunks
                merge. Default: 75th percentile of pairwise similarities.
            max_chars_after_merge: don't merge if the result would exceed this;
                default = ``2 * self.chunk_size`` (twice the windowing target).
        """
        if len(chunks) < 2:
            return chunks

        ceiling = max_chars_after_merge if max_chars_after_merge is not None else (self.chunk_size * 2)
        texts = [c.text for c in chunks]
        try:
            vectors = embedder.embed(texts) if hasattr(embedder, "embed") else embedder(texts)
        except Exception as e:
            logger.warning("refine_with_semantic: embedding failed (%s); returning chunks unchanged", e)
            return chunks

        # Pairwise cosine similarities for adjacent chunks
        def _cosine(a: list[float], b: list[float]) -> float:
            num = sum(x * y for x, y in zip(a, b, strict=False))
            da = sum(x * x for x in a) ** 0.5
            db = sum(x * x for x in b) ** 0.5
            return num / (da * db) if da and db else 0.0

        sims = [_cosine(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]

        if merge_threshold is None:
            sorted_sims = sorted(sims)
            # 75th percentile — top quartile of "topically continuous"
            idx = int(len(sorted_sims) * 0.75)
            merge_threshold = sorted_sims[min(idx, len(sorted_sims) - 1)]

        merged: list[TextChunk] = []
        i = 0
        merged_count = 0
        while i < len(chunks):
            current = chunks[i]
            # Greedy forward-merge as long as the next chunk is similar AND fits
            j = i
            while j + 1 < len(chunks) and sims[j] >= merge_threshold:
                next_chunk = chunks[j + 1]
                proposed_text = current.text + "\n" + next_chunk.text
                if len(proposed_text) > ceiling:
                    break
                # Merge metadata
                cur_speakers = set(current.metadata.get("speakers", []) or [current.metadata.get("speaker", "")])
                next_speakers = set(next_chunk.metadata.get("speakers", []) or [next_chunk.metadata.get("speaker", "")])
                combined_speakers = sorted(s for s in (cur_speakers | next_speakers) if s)
                speaker_chars = {s: 0 for s in combined_speakers}
                # Approximate primary speaker by total char weight from both chunks
                cur_primary = current.metadata.get("primary_speaker", "")
                next_primary = next_chunk.metadata.get("primary_speaker", "")
                speaker_chars[cur_primary] = speaker_chars.get(cur_primary, 0) + len(current.text)
                speaker_chars[next_primary] = speaker_chars.get(next_primary, 0) + len(next_chunk.text)
                primary = max(speaker_chars, key=speaker_chars.get) if speaker_chars else cur_primary

                current = TextChunk(
                    chunk_id=current.chunk_id,
                    document_id=current.document_id,
                    text=proposed_text,
                    index=current.index,
                    total_chunks=current.total_chunks,
                    metadata={
                        **current.metadata,
                        "speakers": combined_speakers,
                        "primary_speaker": primary,
                        "speaker": primary,
                        "turn_count": (current.metadata.get("turn_count", 0) or 0)
                        + (next_chunk.metadata.get("turn_count", 0) or 0),
                        "end_s": next_chunk.metadata.get("end_s", current.metadata.get("end_s", 0)),
                        "strategy": "semantic_merge",
                        "merged_from": (current.metadata.get("merged_from", 0) or 0) + 1,
                    },
                )
                merged_count += 1
                j += 1
            merged.append(current)
            i = j + 1

        # Re-index + rehash post-merge
        for k, c in enumerate(merged):
            c.chunk_id = f"{c.document_id}:chunk-{k}"
            c.index = k
            c.total_chunks = len(merged)
            c.content_hash = hashlib.sha256(c.text.encode()).hexdigest()

        logger.info(
            "refine_with_semantic: %d chunks → %d after %d merges (threshold=%.3f)",
            len(chunks),
            len(merged),
            merged_count,
            merge_threshold,
        )
        return merged

    def chunk_with_semantic_refinement(
        self,
        segments: list[dict],
        document_id: str,
        title: str,
        embedder=None,
        metadata: dict | None = None,
        target_chars: int | None = None,
        pause_threshold_s: float = 1.0,
        fallback_chunk_size: int | None = None,
        inline_speaker_tags: bool = True,
        merge_threshold: float | None = None,
    ) -> list[TextChunk]:
        """One-shot hybrid: multi-speaker windowing + semantic refinement.

        Used by both production ``media_chunks`` asset and the benchmark
        regen script so prod and benchmark are always in lockstep on
        chunking strategy.

        When ``embedder`` is None, falls back to plain multi-speaker
        windowing (no refinement) — useful for unit tests / environments
        without an embedder configured.
        """
        chunks = self.chunk_multi_speaker_segments(
            segments,
            document_id=document_id,
            title=title,
            metadata=metadata,
            target_chars=target_chars,
            pause_threshold_s=pause_threshold_s,
            fallback_chunk_size=fallback_chunk_size,
            inline_speaker_tags=inline_speaker_tags,
        )
        if embedder is None or len(chunks) < 2:
            return chunks
        return self.refine_with_semantic(chunks, embedder, merge_threshold=merge_threshold)

    def passthrough(
        self,
        document_id: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> list[TextChunk]:
        """Wrap a short document as a single chunk without splitting.

        Use for metadata-only or very short documents (members, committees,
        offshore entities) where splitting would add noise.
        """
        # Normalize at ingestion
        title = normalize_text(title) if title else title
        content = normalize_text(content) if content else content

        text = content.strip()
        if not text:
            return []

        CHUNKS_CREATED.labels(strategy="passthrough").inc(1)
        logger.debug("Passthrough chunk document=%s len=%d", document_id, len(text))
        full_text = f"{title}\n\n{text}" if (self.prepend_title and title) else text
        return [
            TextChunk(
                chunk_id=f"{document_id}:chunk-0",
                document_id=document_id,
                text=full_text,
                index=0,
                total_chunks=1,
                metadata={**(metadata or {}), "strategy": "passthrough"},
            )
        ]


# ---------------------------------------------------------------------------
# Standalone helpers (for notebooks / non-Dagster usage)
# ---------------------------------------------------------------------------


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    separators: list[str] | None = None,
    config: ChunkConfig | None = None,
) -> list[str]:
    """Split text into overlapping chunks via LangChain RecursiveCharacterTextSplitter.

    When *config* is provided, ``chunk_size`` and ``chunk_overlap`` are derived
    from the config (target_chars / overlap_tokens * 4) unless explicitly
    overridden by the caller.
    """
    if not text or not text.strip():
        return []

    if config is not None:
        chunk_size = config.target_chars
        chunk_overlap = config.overlap_tokens * 4

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=separators or DEFAULT_SEPARATORS,
        length_function=len,
    )
    return splitter.split_text(text)


def chunk_document(
    document_id: str,
    title: str,
    content: str,
    metadata: dict | None = None,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> list[TextChunk]:
    """Chunk a document into TextChunk objects (standalone, for notebooks)."""
    raw_chunks = chunk_text(content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not raw_chunks:
        return []

    total = len(raw_chunks)
    base_meta = {
        **(metadata or {}),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
    }

    return [
        TextChunk(
            chunk_id=f"{document_id}:chunk-{i}",
            document_id=document_id,
            text=f"{title}\n\n{text}" if title else text,
            index=i,
            total_chunks=total,
            metadata=base_meta,
        )
        for i, text in enumerate(raw_chunks)
    ]
