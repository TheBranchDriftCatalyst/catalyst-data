import { useRef, useEffect, useCallback, useState } from "react";
import { Input, ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { MessageSquare, Search, X, ChevronUp, ChevronDown, Eye, EyeOff } from "lucide-react";
import { useMemo } from "react";
import type { Segment, Word, Mention, ChunkInfo } from "@/types/media";
import { useTranscriptSearch, type TranscriptMatch } from "@/hooks/useTranscriptSearch";
import { useInlineAnnotations } from "@/hooks/useInlineAnnotations";
import AnnotatedText from "@/components/AnnotatedText";
import { speakerIndex, formatTime } from "@/lib/speakers";
import { cn } from "@/lib/utils";

interface TranscriptProps {
  segments: Segment[];
  activeSegmentIndex: number;
  activeWordIndex: number;
  onSeek: (time: number) => void;
  highlightText?: string;
  className?: string;
  /** Resolve a speaker label to its display name. */
  resolveSpeaker?: (label: string | undefined) => string;
  /** Ref forwarded from useTranscriptScroll — attached to the scrollable inner container. */
  transcriptContainerRef?: React.RefObject<HTMLDivElement | null>;
  /** When >= 0, the segment at this index receives a temporary scroll-target highlight. */
  scrollHighlightIndex?: number;
  /**
   * When true, only the segments present in `segments` are shown (the caller
   * has already filtered). A thin divider is rendered when consecutive original
   * indices are non-contiguous.
   */
  isFiltered?: boolean;
  /** Original indices of each segment in the unfiltered list (parallel to `segments`). */
  filteredIndices?: number[];
  /** Entity mentions to render as inline highlights in the transcript. */
  mentions?: Mention[];
  /** Chunk metadata — used to show split strategy per segment. */
  chunks?: ChunkInfo[];
  /** Called when a user clicks an entity highlight in the transcript text. */
  onEntityClick?: (text: string) => void;
}

// Pre-built border classes to avoid dynamic generation
const BORDER_CLASSES = [
  "border-l-[#3b82f6]",
  "border-l-[#ef4444]",
  "border-l-[#22c55e]",
  "border-l-[#f59e0b]",
  "border-l-[#a855f7]",
  "border-l-[#06b6d4]",
  "border-l-[#f97316]",
  "border-l-[#ec4899]",
] as const;

const TEXT_CLASSES = [
  "text-[#3b82f6]",
  "text-[#ef4444]",
  "text-[#22c55e]",
  "text-[#f59e0b]",
  "text-[#a855f7]",
  "text-[#06b6d4]",
  "text-[#f97316]",
  "text-[#ec4899]",
] as const;

export default function Transcript({
  segments,
  activeSegmentIndex,
  activeWordIndex,
  onSeek,
  highlightText,
  className = "",
  resolveSpeaker,
  transcriptContainerRef,
  scrollHighlightIndex = -1,
  isFiltered = false,
  filteredIndices,
  mentions,
  chunks,
  onEntityClick,
}: TranscriptProps) {
  const displayName = (label: string | undefined) =>
    resolveSpeaker ? resolveSpeaker(label) : (label ?? "Unknown");

  // Map each segment to the chunk strategies that overlap its time range
  const strategyBySegment = useMemo(() => {
    if (!chunks || chunks.length === 0) return new Map<number, string[]>();
    const map = new Map<number, string[]>();
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i]!;
      const strategies = new Set<string>();
      for (const c of chunks) {
        const cs = c.metadata.start_s ?? 0;
        const ce = c.metadata.end_s ?? 0;
        if (cs < seg.end && ce > seg.start && c.metadata.strategy) {
          strategies.add(c.metadata.strategy);
        }
      }
      if (strategies.size > 0) map.set(i, [...strategies]);
    }
    return map;
  }, [segments, chunks]);
  const internalContainerRef = useRef<HTMLDivElement>(null);
  const containerRef = transcriptContainerRef ?? internalContainerRef;
  const activeSegRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [showEntityHighlights, setShowEntityHighlights] = useState(true);

  // Inline entity annotations
  const annotationMap = useInlineAnnotations(segments, mentions);

  // Transcript search hook
  const {
    query: searchQuery,
    inputValue: searchInputValue,
    setInputValue: setSearchInputValue,
    clearSearch,
    matches: searchMatches,
    matchCount: searchMatchCount,
    currentMatchIndex,
    nextMatch,
    prevMatch,
    matchesForSegment,
    currentMatchSegmentIndex,
  } = useTranscriptSearch({ segments });

  // Auto-scroll to active segment (from playback)
  useEffect(() => {
    // Don't auto-scroll from playback while user is navigating search results
    if (searchQuery) return;
    if (activeSegRef.current && containerRef.current) {
      const container = containerRef.current;
      const element = activeSegRef.current;
      const containerRect = container.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();

      // Only scroll if element is out of view
      const isVisible =
        elementRect.top >= containerRect.top && elementRect.bottom <= containerRect.bottom;

      if (!isVisible) {
        element.scrollIntoView({
          behavior: "smooth",
          block: "center",
        });
      }
    }
  }, [activeSegmentIndex, searchQuery, containerRef]);

  // Auto-scroll to current search match
  useEffect(() => {
    if (currentMatchSegmentIndex < 0 || !containerRef.current) return;
    const container = containerRef.current;
    // Find the active match element
    const activeEl = container.querySelector("[data-search-active='true']");
    if (activeEl) {
      activeEl.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    // Fallback: scroll to the segment containing the match
    const segEl = container.querySelector(`[data-segment-index="${currentMatchSegmentIndex}"]`);
    if (segEl) {
      segEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [currentMatchIndex, currentMatchSegmentIndex, containerRef]);

  const handleSegmentClick = useCallback(
    (time: number) => {
      onSeek(time);
    },
    [onSeek],
  );

  // Keyboard shortcuts in search input
  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (e.shiftKey) {
          prevMatch();
        } else {
          nextMatch();
        }
      } else if (e.key === "Escape") {
        clearSearch();
        searchInputRef.current?.blur();
      }
    },
    [nextMatch, prevMatch, clearSearch],
  );

  if (segments.length === 0) {
    return (
      <div
        className={cn("flex flex-col items-center justify-center gap-3 text-zinc-500", className)}
      >
        <MessageSquare className="h-8 w-8 text-zinc-700" />
        <p className="text-sm">No transcript available</p>
      </div>
    );
  }

  // Determine the current match object (for identifying the active match in rendering)
  const currentMatch =
    currentMatchIndex >= 0 && currentMatchIndex < searchMatches.length
      ? searchMatches[currentMatchIndex]!
      : null;

  // Group consecutive segments by speaker for visual grouping
  let lastSpeaker: string | null = null;
  let lastOrigIdx = -1;

  return (
    <div className={cn("flex flex-col min-h-0", className)}>
      {/* Search bar */}
      <div className="flex-shrink-0 px-3 py-2 border-b border-white/5">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500 pointer-events-none" />
            <Input
              ref={searchInputRef}
              type="text"
              placeholder="Search transcript..."
              value={searchInputValue}
              onChange={(e) => setSearchInputValue(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              className="h-7 pl-7 pr-7 text-xs bg-surface-0 border-white/10 placeholder:text-zinc-600"
            />
            {searchInputValue && (
              <button
                onClick={clearSearch}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-white/10 text-zinc-500 hover:text-zinc-300 transition-colors"
                aria-label="Clear search"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
          {searchQuery && (
            <div className="flex items-center gap-0.5 flex-shrink-0">
              <span className="text-[10px] text-zinc-500 tabular-nums whitespace-nowrap min-w-[4rem] text-center">
                {searchMatchCount > 0
                  ? `${currentMatchIndex + 1} of ${searchMatchCount}`
                  : "0 results"}
              </span>
              <button
                onClick={prevMatch}
                disabled={searchMatchCount === 0}
                className="p-0.5 rounded hover:bg-white/10 text-zinc-500 hover:text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                aria-label="Previous match"
              >
                <ChevronUp className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={nextMatch}
                disabled={searchMatchCount === 0}
                className="p-0.5 rounded hover:bg-white/10 text-zinc-500 hover:text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                aria-label="Next match"
              >
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
          {mentions && mentions.length > 0 && (
            <button
              onClick={() => setShowEntityHighlights((v) => !v)}
              className={cn(
                "p-1 rounded transition-colors flex-shrink-0",
                showEntityHighlights
                  ? "text-blue-400 hover:bg-blue-900/20"
                  : "text-zinc-600 hover:bg-white/10 hover:text-zinc-400",
              )}
              aria-label={
                showEntityHighlights ? "Hide entity highlights" : "Show entity highlights"
              }
              title={showEntityHighlights ? "Hide entity highlights" : "Show entity highlights"}
            >
              {showEntityHighlights ? (
                <Eye className="h-3.5 w-3.5" />
              ) : (
                <EyeOff className="h-3.5 w-3.5" />
              )}
            </button>
          )}
        </div>
      </div>

      {/* Transcript body */}
      <ScrollArea data-testid="transcript" className="flex-1 min-h-0">
        <div ref={containerRef} className="space-y-1 p-3">
          {segments.map((seg, segIdx) => {
            // Map back to the original index for data attributes and active matching
            const origIdx = filteredIndices ? filteredIndices[segIdx]! : segIdx;
            const isActive = origIdx === activeSegmentIndex;
            const isScrollTarget = origIdx === scrollHighlightIndex;
            const idx = speakerIndex(seg.speaker);
            const borderClass = BORDER_CLASSES[idx]!;
            const textClass = TEXT_CLASSES[idx]!;
            const showSpeakerLabel = seg.speaker !== lastSpeaker;
            lastSpeaker = seg.speaker ?? null;

            // Show a collapsed divider when filtered and indices are non-contiguous
            const showCollapsedDivider =
              isFiltered && lastOrigIdx >= 0 && origIdx - lastOrigIdx > 1;
            lastOrigIdx = origIdx;

            // Get search matches for this segment
            const segSearchMatches = searchQuery ? matchesForSegment(segIdx) : [];

            // Get entity annotations for this segment (if enabled)
            const segAnnotations = showEntityHighlights ? annotationMap.get(segIdx) : undefined;

            return (
              <div key={origIdx}>
                {/* Collapsed segment gap indicator */}
                {showCollapsedDivider && <div className="collapsed-segment-divider" />}

                {/* Speaker label when speaker changes */}
                {showSpeakerLabel && seg.speaker && (
                  <div
                    className={cn(
                      "text-xs font-semibold mt-3 mb-1 flex items-center gap-2",
                      textClass,
                    )}
                  >
                    <span>{displayName(seg.speaker)}</span>
                    <span className="text-zinc-600 font-normal text-[10px] font-mono tabular-nums">
                      {formatTime(seg.start)}
                    </span>
                  </div>
                )}

                {/* Segment block */}
                <div
                  ref={isActive ? activeSegRef : undefined}
                  data-segment-index={origIdx}
                  className={cn(
                    "px-3 py-1.5 rounded-r-md cursor-pointer transition-all duration-200",
                    "border-l-2",
                    borderClass,
                    isActive ? "bg-white/[0.08] segment-active" : "hover:bg-white/[0.04]",
                    isScrollTarget && "scroll-target-highlight",
                  )}
                  onClick={() => handleSegmentClick(seg.start)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      handleSegmentClick(seg.start);
                    }
                  }}
                  aria-label={`Segment at ${formatTime(seg.start)}: ${seg.text.slice(0, 50)}`}
                >
                  {/* Word-level rendering when available */}
                  {seg.words && seg.words.length > 0 ? (
                    <p className="text-sm leading-relaxed">
                      {renderWordsWithSearch(
                        seg.words,
                        segSearchMatches,
                        currentMatch,
                        segIdx,
                        isActive,
                        activeWordIndex,
                        highlightText,
                        onSeek,
                        seg.text,
                        segAnnotations,
                      )}
                    </p>
                  ) : (
                    <p className="text-sm leading-relaxed text-zinc-300">
                      {segSearchMatches.length > 0 ? (
                        highlightSearchInText(seg.text, segSearchMatches, currentMatch, segIdx)
                      ) : highlightText ? (
                        highlightInText(seg.text, highlightText)
                      ) : segAnnotations && segAnnotations.length > 0 ? (
                        <AnnotatedText
                          text={seg.text}
                          annotations={segAnnotations}
                          onEntityClick={onEntityClick}
                        />
                      ) : (
                        seg.text
                      )}
                    </p>
                  )}

                  {/* Segment metadata (subtle, bottom-right) */}
                  <div className="flex items-center gap-2 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    {!showSpeakerLabel && (
                      <span className="text-[10px] text-zinc-600 font-mono tabular-nums">
                        {formatTime(seg.start)}
                      </span>
                    )}
                    <span className="text-[10px] text-zinc-600 font-mono tabular-nums">
                      {Math.round(seg.end - seg.start)}s
                    </span>
                    <span className="text-[10px] text-zinc-600 font-mono">
                      {seg.words?.length ?? seg.text.split(/\s+/).length}w
                    </span>
                    <span className="text-[10px] text-zinc-600 font-mono">{seg.text.length}c</span>
                    {strategyBySegment.get(origIdx)?.map((s) => (
                      <span
                        key={s}
                        className={cn(
                          "text-[9px] px-1.5 py-0.5 rounded-full font-mono",
                          s === "speaker_turn"
                            ? "bg-emerald-500/15 text-emerald-400"
                            : s === "speech_pause_split"
                              ? "bg-amber-500/15 text-amber-400"
                              : "bg-rose-500/15 text-rose-400",
                        )}
                      >
                        {s.replace(/_/g, " ")}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}

/**
 * Render word spans with search match highlighting overlaid.
 * Search matches are computed on the full segment text, so we map character
 * offsets back to individual word boundaries.
 */
function renderWordsWithSearch(
  words: Word[],
  segSearchMatches: TranscriptMatch[],
  currentMatch: TranscriptMatch | null,
  segIdx: number,
  isSegmentActive: boolean,
  activeWordIndex: number,
  highlightText: string | undefined,
  onSeek: (time: number) => void,
  fullText: string,
  annotations?: import("@/hooks/useInlineAnnotations").InlineAnnotation[],
): React.ReactNode {
  if (segSearchMatches.length === 0) {
    // No search matches — use normal word rendering with optional entity annotations
    // Pre-compute word positions for annotation matching
    let wordPositionsForAnnotations: { start: number; end: number }[] | undefined;
    if (annotations && annotations.length > 0) {
      wordPositionsForAnnotations = [];
      let p = 0;
      for (const w of words) {
        const wText = w.word;
        const i = fullText.indexOf(wText, p);
        if (i >= 0) {
          wordPositionsForAnnotations.push({ start: i, end: i + wText.length });
          p = i + wText.length;
        } else {
          wordPositionsForAnnotations.push({ start: p, end: p + wText.length });
          p += wText.length;
        }
      }
    }

    return words.map((word, wIdx) => {
      // Check if this word falls within any entity annotation span.
      // Words may have leading whitespace (" Trump") — trim it for matching
      // since annotations reference the text without leading spaces.
      let entityType: string | undefined;
      if (wordPositionsForAnnotations && annotations) {
        const wp = wordPositionsForAnnotations[wIdx];
        if (wp) {
          const leadingSpaces = word.word.length - word.word.trimStart().length;
          const trimmedStart = wp.start + leadingSpaces;
          const ann = annotations.find((a) => trimmedStart >= a.start && trimmedStart < a.end);
          if (ann) entityType = ann.entityType;
        }
      }
      return (
        <WordSpan
          key={wIdx}
          word={word}
          isActive={isSegmentActive && wIdx === activeWordIndex}
          highlightText={highlightText}
          onClick={() => onSeek(word.start)}
          entityType={entityType}
        />
      );
    });
  }

  // Build a character-level map of the full text to identify which ranges are
  // search matches and which is the active match. We then split word text
  // accordingly.
  //
  // First, build a map from character position → which word it belongs to.
  // Words in whisper output have their own .word property (which includes
  // leading spaces). We reconstruct positions by scanning the full text.
  const wordPositions: { start: number; end: number }[] = [];
  let pos = 0;
  for (const w of words) {
    const wText = w.word;
    const idx = fullText.indexOf(wText, pos);
    if (idx >= 0) {
      wordPositions.push({ start: idx, end: idx + wText.length });
      pos = idx + wText.length;
    } else {
      // Fallback: assume contiguous
      wordPositions.push({ start: pos, end: pos + wText.length });
      pos += wText.length;
    }
  }

  // For each word, determine what parts overlap with search matches
  const result: React.ReactNode[] = [];
  for (let wIdx = 0; wIdx < words.length; wIdx++) {
    const word = words[wIdx]!;
    const wp = wordPositions[wIdx]!;
    const isWordActive = isSegmentActive && wIdx === activeWordIndex;

    // Find overlapping search matches
    const overlapping = segSearchMatches.filter(
      (m) => m.startChar < wp.end && m.endChar > wp.start,
    );

    if (overlapping.length === 0) {
      // No search match — render normally
      result.push(
        <WordSpan
          key={wIdx}
          word={word}
          isActive={isWordActive}
          highlightText={highlightText}
          onClick={() => onSeek(word.start)}
        />,
      );
    } else {
      // Split the word text into matched / unmatched fragments
      const fragments = splitWordByMatches(word.word, wp.start, overlapping, currentMatch, segIdx);
      result.push(
        <span
          key={wIdx}
          className={cn(
            "karaoke-word cursor-pointer rounded-sm",
            isWordActive && "karaoke-word-active font-medium px-0.5",
            !isWordActive && "text-zinc-300 hover:text-zinc-100",
          )}
          onClick={(e) => {
            e.stopPropagation();
            onSeek(word.start);
          }}
          title={`${formatTime(word.start)} (${(word.probability * 100).toFixed(0)}% conf)`}
        >
          {fragments}
        </span>,
      );
    }
  }
  return result;
}

/** Split a word's text into fragments based on search match boundaries. */
function splitWordByMatches(
  wordText: string,
  wordStart: number,
  matches: TranscriptMatch[],
  currentMatch: TranscriptMatch | null,
  segIdx: number,
): React.ReactNode[] {
  // Build cut points within the word (relative to word start)
  const cuts = new Set<number>();
  cuts.add(0);
  cuts.add(wordText.length);
  for (const m of matches) {
    const relStart = Math.max(0, m.startChar - wordStart);
    const relEnd = Math.min(wordText.length, m.endChar - wordStart);
    cuts.add(relStart);
    cuts.add(relEnd);
  }
  const sortedCuts = Array.from(cuts).sort((a, b) => a - b);

  const fragments: React.ReactNode[] = [];
  for (let i = 0; i < sortedCuts.length - 1; i++) {
    const from = sortedCuts[i]!;
    const to = sortedCuts[i + 1]!;
    if (from === to) continue;
    const fragText = wordText.slice(from, to);
    const absFrom = wordStart + from;

    // Check if this fragment is within a search match
    const isInMatch = matches.some((m) => absFrom >= m.startChar && absFrom < m.endChar);
    // Check if this is the active/current match
    const isActiveMatch =
      isInMatch &&
      currentMatch !== null &&
      currentMatch.segmentIndex === segIdx &&
      matches.some(
        (m) =>
          m.startChar === currentMatch.startChar &&
          m.endChar === currentMatch.endChar &&
          m.segmentIndex === currentMatch.segmentIndex &&
          absFrom >= m.startChar &&
          absFrom < m.endChar,
      );

    if (isInMatch) {
      fragments.push(
        <mark
          key={i}
          className={cn(
            "rounded-sm px-0",
            isActiveMatch ? "transcript-search-active" : "transcript-search-match",
          )}
          data-search-active={isActiveMatch ? "true" : undefined}
        >
          {fragText}
        </mark>,
      );
    } else {
      fragments.push(<span key={i}>{fragText}</span>);
    }
  }
  return fragments;
}

/** Highlight search matches in plain text (no word-level data). */
function highlightSearchInText(
  text: string,
  matches: TranscriptMatch[],
  currentMatch: TranscriptMatch | null,
  segIdx: number,
): React.ReactNode {
  if (matches.length === 0) return text;

  // Build cut points
  const cuts = new Set<number>();
  cuts.add(0);
  cuts.add(text.length);
  for (const m of matches) {
    cuts.add(m.startChar);
    cuts.add(m.endChar);
  }
  const sortedCuts = Array.from(cuts).sort((a, b) => a - b);

  const parts: React.ReactNode[] = [];
  for (let i = 0; i < sortedCuts.length - 1; i++) {
    const from = sortedCuts[i]!;
    const to = sortedCuts[i + 1]!;
    if (from === to) continue;
    const frag = text.slice(from, to);

    const isInMatch = matches.some((m) => from >= m.startChar && from < m.endChar);
    const isActiveMatch =
      isInMatch &&
      currentMatch !== null &&
      currentMatch.segmentIndex === segIdx &&
      matches.some(
        (m) =>
          m.startChar === currentMatch.startChar &&
          m.endChar === currentMatch.endChar &&
          m.segmentIndex === currentMatch.segmentIndex &&
          from >= m.startChar &&
          from < m.endChar,
      );

    if (isInMatch) {
      parts.push(
        <mark
          key={i}
          className={cn(
            "rounded-sm px-0.5",
            isActiveMatch ? "transcript-search-active" : "transcript-search-match",
          )}
          data-search-active={isActiveMatch ? "true" : undefined}
        >
          {frag}
        </mark>,
      );
    } else {
      parts.push(<span key={i}>{frag}</span>);
    }
  }
  return parts;
}

/** Individual word span with active highlighting (karaoke effect) */
function WordSpan({
  word,
  isActive,
  highlightText,
  onClick,
  entityType,
}: {
  word: Word;
  isActive: boolean;
  highlightText?: string;
  onClick: () => void;
  entityType?: string;
}) {
  const text = word.word;
  const isHighlighted = highlightText && text.toLowerCase().includes(highlightText.toLowerCase());

  return (
    <span
      className={cn(
        "karaoke-word cursor-pointer rounded-sm",
        isActive && "karaoke-word-active font-medium px-0.5",
        isHighlighted && !isActive && "bg-amber-900/50 text-amber-200",
        !isActive &&
          !isHighlighted &&
          entityType &&
          `entity-highlight entity-highlight-${entityType}`,
        !isActive && !isHighlighted && !entityType && "text-zinc-300 hover:text-zinc-100",
      )}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={`${formatTime(word.start)} (${(word.probability * 100).toFixed(0)}% conf)`}
    >
      {text}
    </span>
  );
}

/** Highlight matching text within a string (entity highlight, amber) */
function highlightInText(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const regex = new RegExp(`(${escapeRegex(query)})`, "gi");
  const parts = text.split(regex);
  return parts.map((part, i) =>
    regex.test(part) ? (
      <mark key={i} className="bg-amber-900/50 text-amber-200 rounded-sm px-0.5">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
