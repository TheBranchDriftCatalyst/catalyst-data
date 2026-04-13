import { useRef, useCallback, useMemo } from "react";

export interface TimeRange {
  start: number;
  end: number;
}

interface UseFilteredPlaybackOptions {
  /** Sorted, non-overlapping time ranges representing matching segments. */
  ranges: TimeRange[];
  /** Seek function (e.g. playerRef.current.seek). */
  seek: (time: number) => void;
  /** Pause function (e.g. playerRef.current.pause). */
  pause: () => void;
  /** Whether filtered playback mode is enabled. */
  enabled: boolean;
}

interface UseFilteredPlaybackResult {
  /** Whether filtered playback is currently active. */
  isFilteredPlayback: boolean;
  /** Index of the range the current time falls within, or -1. */
  activeRangeIndex: number;
  /**
   * Call this on every time update. If current time falls in a gap between
   * ranges, it auto-seeks to the start of the next range.
   */
  onTimeUpdate: (currentTime: number) => void;
}

/**
 * Hook that implements "highlight reel" playback: when enabled, monitors
 * playback time and auto-skips gaps between matching segments so the user
 * only hears/sees the relevant portions.
 *
 * The ranges array must be sorted by `start` and non-overlapping.
 */
export function useFilteredPlayback({
  ranges,
  seek,
  pause,
  enabled,
}: UseFilteredPlaybackOptions): UseFilteredPlaybackResult {
  // Track the last seek target to avoid repeated seeks to the same position
  const lastSeekTarget = useRef<number | null>(null);

  // Merge overlapping/adjacent ranges and sort them
  const mergedRanges = useMemo(() => {
    if (ranges.length === 0) return [];
    const sorted = [...ranges].sort((a, b) => a.start - b.start);
    const merged: TimeRange[] = [{ ...sorted[0]! }];
    for (let i = 1; i < sorted.length; i++) {
      const prev = merged[merged.length - 1]!;
      const curr = sorted[i]!;
      // Merge if overlapping or adjacent (within 0.1s)
      if (curr.start <= prev.end + 0.1) {
        prev.end = Math.max(prev.end, curr.end);
      } else {
        merged.push({ ...curr });
      }
    }
    return merged;
  }, [ranges]);

  /**
   * Binary search: find the index of the range containing `time`,
   * or the index of the next range after `time`, or -1 if past all ranges.
   */
  const findRangeIndex = useCallback(
    (time: number): { inRange: boolean; index: number } => {
      let lo = 0;
      let hi = mergedRanges.length - 1;

      while (lo <= hi) {
        const mid = (lo + hi) >>> 1;
        const r = mergedRanges[mid]!;
        if (time >= r.start && time < r.end) {
          return { inRange: true, index: mid };
        }
        if (time >= r.end) {
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }

      // `lo` is now the index of the first range starting after `time`
      if (lo < mergedRanges.length) {
        return { inRange: false, index: lo };
      }
      return { inRange: false, index: -1 };
    },
    [mergedRanges],
  );

  const activeRangeIndexRef = useRef(-1);

  const onTimeUpdate = useCallback(
    (currentTime: number) => {
      if (!enabled || mergedRanges.length === 0) {
        activeRangeIndexRef.current = -1;
        return;
      }

      const { inRange, index } = findRangeIndex(currentTime);

      if (inRange) {
        activeRangeIndexRef.current = index;
        lastSeekTarget.current = null; // Reset: we're inside a valid range
        return;
      }

      // We're in a gap (or past all ranges)
      if (index === -1) {
        // Past all ranges — pause playback
        activeRangeIndexRef.current = -1;
        pause();
        return;
      }

      // We're in a gap before range[index]. Seek to it, but only if we
      // haven't already issued this same seek (prevents infinite loop
      // when the seek hasn't completed yet).
      const target = mergedRanges[index]!.start;
      if (lastSeekTarget.current !== target) {
        lastSeekTarget.current = target;
        activeRangeIndexRef.current = index;
        seek(target);
      }
    },
    [enabled, mergedRanges, findRangeIndex, seek, pause],
  );

  return {
    isFilteredPlayback: enabled && mergedRanges.length > 0,
    activeRangeIndex: activeRangeIndexRef.current,
    onTimeUpdate,
  };
}
