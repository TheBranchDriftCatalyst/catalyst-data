import { useMemo } from "react";
import type { Segment } from "@/types/media";

interface MediaSyncResult {
  activeSegmentIndex: number;
  activeWordIndex: number;
  activeSegment: Segment | null;
}

/**
 * Binary search for the segment containing `currentTime`.
 * Segments are assumed sorted by `start`.
 */
function findSegmentIndex(segments: Segment[], time: number): number {
  let lo = 0;
  let hi = segments.length - 1;
  let result = -1;

  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    const seg = segments[mid]!;
    if (time >= seg.start && time < seg.end) {
      return mid;
    }
    if (time >= seg.end) {
      lo = mid + 1;
    } else {
      // time < seg.start — this might be a gap; track closest segment after
      result = mid;
      hi = mid - 1;
    }
  }

  // If we didn't find an exact match, check if we're in a gap
  // Return the segment we're between (prefer the one about to start)
  if (result !== -1 && segments[result]) {
    const seg = segments[result]!;
    // Only return if we're close (within 0.5s of the next segment)
    if (seg.start - time < 0.5) return result;
  }

  return -1;
}

/**
 * Binary search for the word within a segment containing `currentTime`.
 */
function findWordIndex(segment: Segment, time: number): number {
  const words = segment.words;
  if (!words || words.length === 0) return -1;

  let lo = 0;
  let hi = words.length - 1;

  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    const w = words[mid]!;
    if (time >= w.start && time < w.end) {
      return mid;
    }
    if (time >= w.end) {
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }

  return -1;
}

/**
 * Hook: tracks which segment and word are active at the given currentTime.
 * Uses binary search for O(log n) performance on every timeupdate tick.
 */
export function useMediaSync(segments: Segment[], currentTime: number): MediaSyncResult {
  return useMemo(() => {
    if (segments.length === 0) {
      return { activeSegmentIndex: -1, activeWordIndex: -1, activeSegment: null };
    }

    const segIdx = findSegmentIndex(segments, currentTime);
    if (segIdx === -1) {
      return { activeSegmentIndex: -1, activeWordIndex: -1, activeSegment: null };
    }

    const seg = segments[segIdx]!;
    const wordIdx = findWordIndex(seg, currentTime);

    return {
      activeSegmentIndex: segIdx,
      activeWordIndex: wordIdx,
      activeSegment: seg,
    };
  }, [segments, currentTime]);
}
