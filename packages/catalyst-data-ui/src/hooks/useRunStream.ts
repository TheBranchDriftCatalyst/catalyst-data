/**
 * Read a bench run's audit log via the DuckDB-backed
 * ``GET /viewer/api/bench/runs/<id>/events`` endpoint and re-poll on a
 * fixed interval.
 *
 * CD-jzkg Phase 3: the legacy ``events.jsonl`` fallback was removed —
 * DuckDB / parquet is the only path. On 404 the hook surfaces the error
 * to the caller (the SPA renders an empty state); on success it logs
 * one informational line per poll so the operator can spot read churn
 * in DevTools without scraping the network tab.
 *
 * Run selection: caller can pin a specific ``runId`` via the optional
 * argument (e.g. from a ``?run=<id>`` URL param). When omitted, the
 * hook follows whatever ``/runs`` reports as ``latest`` — which the
 * backend defines as the currently-in-flight local run if any, else
 * the newest archived run in S3.
 */
import { useEffect, useState } from "react";

import type { RunEvent } from "@/types/benchmark";

interface UseRunStreamResult {
  events: RunEvent[];
  /** The run_id whose events are currently in `events`. Drives header
   *  badges + e2e selectors that need to assert "we're looking at run X". */
  runId: string | null;
  /** True once we've successfully fetched at least one batch. */
  connected: boolean;
  error: string | null;
}

const POLL_INTERVAL_MS = 3_000;

async function latestRunId(): Promise<string | null> {
  try {
    const res = await fetch("/viewer/api/bench/runs");
    if (!res.ok) return null;
    const body = (await res.json()) as { latest: string | null };
    return body.latest ?? null;
  } catch {
    return null;
  }
}

/** Fetch the audit log for ``runId`` from the DuckDB endpoint.
 *  Returns ``null`` on non-2xx (typically 404 = "no parquet for this run yet")
 *  so callers can render an empty state. Returns an empty array on a
 *  successful but empty response (freshly-started run). */
async function fetchEvents(runId: string): Promise<RunEvent[] | null> {
  const res = await fetch(`/viewer/api/bench/runs/${encodeURIComponent(runId)}/events?limit=50000`);
  if (!res.ok) {
    return null;
  }
  const text = await res.text();
  return text
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line) as RunEvent;
      } catch {
        return null;
      }
    })
    .filter((e): e is RunEvent => e !== null);
}

/**
 * @param pinnedRunId  Optional explicit run_id to read (e.g. from a
 *   `?run=...` URL param). Pass `null` / `undefined` to follow `/runs`'
 *   `latest`.
 */
export function useRunStream(pinnedRunId?: string | null): UseRunStreamResult {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [runId, setRunId] = useState<string | null>(pinnedRunId ?? null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timeout: number | null = null;

    const tick = async () => {
      // Resolve the run we're going to read this tick. If the caller
      // pinned one, honor it forever; otherwise refresh against /runs
      // every poll so the UI follows the live run when it appears.
      const targetRunId = pinnedRunId ?? (await latestRunId());
      if (cancelled) return;
      if (!targetRunId) {
        setError("no run found");
        setConnected(false);
        setRunId(null);
        timeout = window.setTimeout(tick, POLL_INTERVAL_MS);
        return;
      }

      const next = await fetchEvents(targetRunId);
      if (cancelled) return;

      // Always reflect the run we *attempted* — useful for the run-picker
      // dropdown to show the current selection even before any events land.
      setRunId(targetRunId);

      if (next === null) {
        setError("no events for run");
        setConnected(false);
        timeout = window.setTimeout(tick, POLL_INTERVAL_MS);
        return;
      }

      console.log(`[audit-log] reader=duckdb run=${targetRunId} count=${next.length}`);
      setEvents(next);
      setConnected(true);
      setError(null);
      timeout = window.setTimeout(tick, POLL_INTERVAL_MS);
    };

    tick();

    return () => {
      cancelled = true;
      if (timeout !== null) window.clearTimeout(timeout);
    };
  }, [pinnedRunId]);

  return { events, runId, connected, error };
}
