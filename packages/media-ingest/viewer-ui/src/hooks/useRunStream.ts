/**
 * Read the latest run's events.jsonl from S3, with optional polling.
 *
 * Replaces an earlier WebSocket+SSE live-tail implementation. The harness
 * already writes events.jsonl as it runs, so the same data is available
 * via a plain GET — no proxy, no bus port, no reconnect logic. Polling
 * every few seconds gets us "live-ish" without any of the WS plumbing.
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

async function fetchEvents(runId: string): Promise<RunEvent[]> {
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
      const next = await fetchEvents(runId);
      if (cancelled) return;
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
  }, []);

  return { events, connected, error };
}
