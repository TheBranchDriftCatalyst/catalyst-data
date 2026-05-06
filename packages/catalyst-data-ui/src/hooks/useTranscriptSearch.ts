import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import type { Segment } from "@/types/media";

/** A single match within the transcript. */
export interface TranscriptMatch {
  /** Index of the segment containing this match. */
  segmentIndex: number;
  /** Character offset within the segment's full text where the match starts. */
  startChar: number;
  /** Character offset within the segment's full text where the match ends (exclusive). */
  endChar: number;
}

interface UseTranscriptSearchOptions {
  segments: Segment[];
}

interface UseTranscriptSearchResult {
  /** The current search query (debounced). */
  query: string;
  /** The raw (un-debounced) input value for controlled input binding. */
  inputValue: string;
  /** Set the raw input value — debouncing happens internally. */
  setInputValue: (value: string) => void;
  /** Clear the search entirely. */
  clearSearch: () => void;
  /** All matches for the current query. */
  matches: TranscriptMatch[];
  /** Total number of matches. */
  matchCount: number;
  /** Index of the "current" (focused) match within `matches`, or -1 if none. */
  currentMatchIndex: number;
  /** Navigate to the next match. */
  nextMatch: () => void;
  /** Navigate to the previous match. */
  prevMatch: () => void;
  /** Check whether a given segment contains any match. */
  segmentHasMatch: (segmentIndex: number) => boolean;
  /** Get all matches within a specific segment. */
  matchesForSegment: (segmentIndex: number) => TranscriptMatch[];
  /** The segment index of the current match (for scrolling), or -1. */
  currentMatchSegmentIndex: number;
}

/**
 * Hook providing full-text search within transcript segments with
 * debounced input, match navigation, and per-segment match lookup.
 */
export function useTranscriptSearch({
  segments,
}: UseTranscriptSearchOptions): UseTranscriptSearchResult {
  const [inputValue, setInputValue] = useState("");
  const [query, setQuery] = useState("");
  const [currentMatchIndex, setCurrentMatchIndex] = useState(-1);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce input → query
  useEffect(() => {
    if (debounceRef.current !== null) clearTimeout(debounceRef.current);
    if (inputValue.trim() === "") {
      setQuery("");
      setCurrentMatchIndex(-1);
      return;
    }
    debounceRef.current = setTimeout(() => {
      setQuery(inputValue.trim());
    }, 300);
    return () => {
      if (debounceRef.current !== null) clearTimeout(debounceRef.current);
    };
  }, [inputValue]);

  // Compute all matches when query or segments change
  const matches = useMemo<TranscriptMatch[]>(() => {
    if (!query) return [];
    const lowerQuery = query.toLowerCase();
    const results: TranscriptMatch[] = [];

    for (let segIdx = 0; segIdx < segments.length; segIdx++) {
      const text = segments[segIdx]!.text.toLowerCase();
      let searchFrom = 0;
      while (searchFrom < text.length) {
        const pos = text.indexOf(lowerQuery, searchFrom);
        if (pos === -1) break;
        results.push({
          segmentIndex: segIdx,
          startChar: pos,
          endChar: pos + lowerQuery.length,
        });
        searchFrom = pos + 1; // allow overlapping matches
      }
    }
    return results;
  }, [segments, query]);

  // Reset current match when matches change
  useEffect(() => {
    setCurrentMatchIndex(matches.length > 0 ? 0 : -1);
  }, [matches]);

  // Build a lookup set of segments that have matches
  const segmentMatchMap = useMemo(() => {
    const map = new Map<number, TranscriptMatch[]>();
    for (const m of matches) {
      const existing = map.get(m.segmentIndex);
      if (existing) {
        existing.push(m);
      } else {
        map.set(m.segmentIndex, [m]);
      }
    }
    return map;
  }, [matches]);

  const segmentHasMatch = useCallback(
    (segmentIndex: number) => segmentMatchMap.has(segmentIndex),
    [segmentMatchMap],
  );

  const matchesForSegment = useCallback(
    (segmentIndex: number) => segmentMatchMap.get(segmentIndex) ?? [],
    [segmentMatchMap],
  );

  const nextMatch = useCallback(() => {
    if (matches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev + 1) % matches.length);
  }, [matches.length]);

  const prevMatch = useCallback(() => {
    if (matches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev - 1 + matches.length) % matches.length);
  }, [matches.length]);

  const clearSearch = useCallback(() => {
    setInputValue("");
    setQuery("");
    setCurrentMatchIndex(-1);
  }, []);

  const currentMatchSegmentIndex =
    currentMatchIndex >= 0 && currentMatchIndex < matches.length
      ? matches[currentMatchIndex]!.segmentIndex
      : -1;

  return {
    query,
    inputValue,
    setInputValue,
    clearSearch,
    matches,
    matchCount: matches.length,
    currentMatchIndex,
    nextMatch,
    prevMatch,
    segmentHasMatch,
    matchesForSegment,
    currentMatchSegmentIndex,
  };
}
