import { useState, useRef, useCallback, useMemo } from "react";
import type { Segment } from "@/types/media";

interface UseTranscriptScrollOptions {
  segments: Segment[];
}

interface UseTranscriptScrollResult {
  /** Scroll the transcript container so the segment containing `time` is visible, with a brief highlight. */
  scrollToTimestamp: (time: number) => void;
  /** Filter to only show segments at the given indices (highlight-reel mode). */
  filterSegments: (segmentIndices: number[]) => void;
  /** Clear any active filter, restoring all segments. */
  clearFilter: () => void;
  /** The currently visible set of segments (all if no filter, subset if filtered). */
  filteredSegments: Segment[];
  /** Original indices of the filtered segments within the full segments array. */
  filteredIndices: number[];
  /** Whether a filter is currently active. */
  isFiltered: boolean;
  /** Ref to attach to the scrollable transcript container. */
  transcriptRef: React.RefObject<HTMLDivElement | null>;
  /** Index of a segment that should show a brief "scroll target" highlight, or -1. */
  scrollHighlightIndex: number;
}

/**
 * Hook providing two transcript navigation modes:
 *
 * 1. **scroll-to-timestamp** — smooth-scrolls to the segment containing a given
 *    timestamp and briefly highlights it (e.g. when clicking a single mention).
 *
 * 2. **filter-to-subset** — collapses non-matching segments so only a subset is
 *    visible (e.g. when clicking an entity group to see its "highlight reel").
 */
export function useTranscriptScroll({
  segments,
}: UseTranscriptScrollOptions): UseTranscriptScrollResult {
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const [filterIndices, setFilterIndices] = useState<number[] | null>(null);
  const [scrollHighlightIndex, setScrollHighlightIndex] = useState(-1);

  // ── Filtered segment computation ────────────────────────────────────
  const { filteredSegments, filteredIndices } = useMemo(() => {
    if (!filterIndices) {
      return {
        filteredSegments: segments,
        filteredIndices: segments.map((_, i) => i),
      };
    }
    const indexSet = new Set(filterIndices);
    const segs: Segment[] = [];
    const indices: number[] = [];
    for (let i = 0; i < segments.length; i++) {
      if (indexSet.has(i)) {
        segs.push(segments[i]!);
        indices.push(i);
      }
    }
    return { filteredSegments: segs, filteredIndices: indices };
  }, [segments, filterIndices]);

  // ── Mode 1: scroll-to-timestamp ─────────────────────────────────────
  const scrollToTimestamp = useCallback(
    (time: number) => {
      // Binary search for the segment containing `time`
      let targetIdx = -1;
      let lo = 0;
      let hi = segments.length - 1;
      while (lo <= hi) {
        const mid = (lo + hi) >>> 1;
        const seg = segments[mid]!;
        if (time >= seg.start && time < seg.end) {
          targetIdx = mid;
          break;
        }
        if (time >= seg.end) {
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }

      // If no exact match, find the closest segment starting after `time`
      if (targetIdx === -1) {
        for (let i = 0; i < segments.length; i++) {
          if (segments[i]!.start >= time) {
            targetIdx = i;
            break;
          }
        }
      }
      if (targetIdx === -1) return;

      // Scroll the element into view
      const container = transcriptRef.current;
      if (container) {
        const segEl = container.querySelector(`[data-segment-index="${targetIdx}"]`);
        if (segEl) {
          segEl.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }

      // Flash highlight
      setScrollHighlightIndex(targetIdx);
      setTimeout(() => setScrollHighlightIndex(-1), 1500);
    },
    [segments],
  );

  // ── Mode 2: filter-to-subset ────────────────────────────────────────
  const filterSegmentsAction = useCallback((segmentIndices: number[]) => {
    setFilterIndices(segmentIndices.length > 0 ? segmentIndices : null);
  }, []);

  const clearFilter = useCallback(() => {
    setFilterIndices(null);
  }, []);

  return {
    scrollToTimestamp,
    filterSegments: filterSegmentsAction,
    clearFilter,
    filteredSegments,
    filteredIndices,
    isFiltered: filterIndices !== null,
    transcriptRef,
    scrollHighlightIndex,
  };
}
