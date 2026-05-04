/**
 * Read the latest run's audit log, with optional polling.
 *
 * Phase 2 of CD-jzkg: prefer the DuckDB-backed
 * ``GET /viewer/api/bench/runs/<id>/events`` endpoint (parameterised facets,
 * served from ``events.parquet``) and fall back to the legacy
 * ``events.jsonl`` endpoint on 404 / non-2xx. Same RunEvent[] shape — no
 * other consumer changes.
 *
 * The fallback path is the explicit safety net during the dual-read
 * validation window; Phase 3 strangles it once three consecutive runs
 * show zero fallback hits in the server-side diagnostics counter.
 *
 * Console logs fire on EVERY poll cycle (informational, not error-only)
 * so the validation window can be watched in DevTools without scraping
 * the network tab.
 */
import { useEffect, useState } from "react";

import type { RunEvent } from "@/types/benchmark";

interface UseRunStreamResult {
  events: RunEvent[];
  /** True once we've successfully fetched at least one batch. Mirrors the
   *  previous hook's semantics so existing callers (StateInspector) render
   *  the right "connected" indicator. */
  connected: boolean;
  error: string | null;
}

const POLL_INTERVAL_MS = 3_000;

/** Result of a single fetch attempt — separates "no events yet" (empty list)
 *  from "endpoint did not resolve" (null) so the caller can decide whether
 *  to fall back. */
type FetchResult = { events: RunEvent[]; reason?: string } | null;

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

/** Try the DuckDB-backed endpoint. Returns ``null`` on 404 / non-2xx so the
 *  caller falls back to jsonl; returns ``{ events: [...] }`` on success
 *  (possibly empty for a freshly-started run). */
async function fetchDuckDB(runId: string): Promise<FetchResult> {
  try {
    const res = await fetch(
      `/viewer/api/bench/runs/${encodeURIComponent(runId)}/events?limit=50000`,
    );
    if (!res.ok) {
      // 404 is the expected "no parquet for this run yet" signal; anything
      // else is unexpected but we still fall back to jsonl rather than
      // surface a hard error to the operator mid-run.
      return null;
    }
    const text = await res.text();
    const events = text
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
    return { events };
  } catch (e) {
    return { events: [], reason: `duckdb fetch threw: ${(e as Error).message}` };
  }
}

async function fetchJsonl(runId: string): Promise<RunEvent[]> {
  try {
    const res = await fetch(`/viewer/api/bench/runs/${encodeURIComponent(runId)}/events.jsonl`);
    if (!res.ok) return [];
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
  } catch {
    return [];
  }
}

/** Fire-and-forget: tell the server-side diagnostics counter that we just
 *  fell back. Server-side because the frontend's polling state resets on
 *  every page load — Phase 3 needs a durable signal. */
function reportFallback(runId: string, reason: string): void {
  void fetch("/viewer/api/bench/diagnostics/fallback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, reason }),
  }).catch(() => {
    /* swallow — diagnostics is best-effort */
  });
}

export function useRunStream(): UseRunStreamResult {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timeout: number | null = null;

    const tick = async () => {
      const runId = await latestRunId();
      if (cancelled) return;
      if (!runId) {
        setError("no run found");
        setConnected(false);
        timeout = window.setTimeout(tick, POLL_INTERVAL_MS);
        return;
      }

      // Phase 2 dual-read: try DuckDB first, fall back to jsonl on 404.
      const duck = await fetchDuckDB(runId);
      if (cancelled) return;

      if (duck && !duck.reason) {
        console.log(`[audit-log] reader=duckdb run=${runId} count=${duck.events.length}`);
        setEvents(duck.events);
        setConnected(true);
        setError(null);
        timeout = window.setTimeout(tick, POLL_INTERVAL_MS);
        return;
      }

      const reason = duck?.reason ?? "duckdb returned 404 or empty";
      const fallback = await fetchJsonl(runId);
      if (cancelled) return;
      console.warn(
        `[audit-log] reader=jsonl-fallback run=${runId} count=${fallback.length} reason="${reason}"`,
      );
      reportFallback(runId, reason);
      setEvents(fallback);
      setConnected(true);
      setError(null);
      timeout = window.setTimeout(tick, POLL_INTERVAL_MS);
    };

    tick();

    return () => {
      cancelled = true;
      if (timeout !== null) window.clearTimeout(timeout);
    };
  }, []);

  return { events, connected, error };
}
