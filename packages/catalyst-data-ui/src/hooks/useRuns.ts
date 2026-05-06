/**
 * Lightweight poll over `/viewer/api/bench/runs` so the UI's run-picker
 * dropdown stays in sync with what the backend can serve. 30s interval
 * — runs don't appear that often, and the live run is already covered
 * by `useRunStream`'s 3s tick on `/runs?latest`.
 */
import { useEffect, useState } from "react";

const POLL_INTERVAL_MS = 30_000;

export interface RunsListing {
  /** Newest first. Includes the live run at index 0 if one is in flight. */
  runs: string[];
  /** The run_id of the newest item in `runs`, or null if empty. */
  latest: string | null;
  /** Set when a run is currently writing parquet shards locally. Used
   *  to render the "live" badge next to that entry in the picker. */
  live: string | null;
}

export function useRuns(): RunsListing {
  const [state, setState] = useState<RunsListing>({ runs: [], latest: null, live: null });

  useEffect(() => {
    let cancelled = false;
    let timeout: number | null = null;

    const tick = async () => {
      try {
        const res = await fetch("/viewer/api/bench/runs");
        if (!cancelled && res.ok) {
          const body = (await res.json()) as RunsListing;
          setState({
            runs: body.runs ?? [],
            latest: body.latest ?? null,
            live: body.live ?? null,
          });
        }
      } catch {
        // Network blip — keep the previous list, retry next tick.
      }
      if (!cancelled) timeout = window.setTimeout(tick, POLL_INTERVAL_MS);
    };

    tick();

    return () => {
      cancelled = true;
      if (timeout !== null) window.clearTimeout(timeout);
    };
  }, []);

  return state;
}
