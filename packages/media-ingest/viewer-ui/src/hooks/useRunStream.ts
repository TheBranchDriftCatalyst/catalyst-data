/**
 * Subscribe to the harness's run-bus over WebSocket.
 *
 * On mount: ask the bench API for the active bus port; if a run is live,
 * open `ws://127.0.0.1:<port>/stream` and stream every event into state.
 * New connections receive the existing `events.jsonl` first (replay),
 * then the live tail. When the WS is unreachable (post-run / offline),
 * fall back to fetching the archived JSONL of the latest run from S3
 * via `/viewer/api/bench/runs/<id>/events.jsonl`.
 */

import { useEffect, useState } from "react";

import type { RunEvent } from "@/types/benchmark";

interface UseRunStreamResult {
  events: RunEvent[];
  connected: boolean;
  error: string | null;
}

interface BusPortResp {
  port: number | null;
  active: boolean;
}

async function busPort(): Promise<number | null> {
  try {
    const res = await fetch("/viewer/api/bench/bus-port");
    if (!res.ok) return null;
    const body = (await res.json()) as BusPortResp;
    return body.active ? body.port : null;
  } catch {
    return null;
  }
}

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

async function fetchHistoricalEvents(): Promise<RunEvent[]> {
  // Fallback: pull the archived events.jsonl for the latest run from S3
  // via the bench API. Live tail goes through the WS bus instead.
  const runId = await latestRunId();
  if (!runId) return [];
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
    let sse: EventSource | null = null;

    (async () => {
      const port = await busPort();
      if (cancelled) return;

      if (port === null) {
        // No live bus — load the latest archived run from S3.
        const historical = await fetchHistoricalEvents();
        if (!cancelled) {
          setEvents(historical);
          setError("offline (replay)");
        }
        return;
      }

      // Use Server-Sent Events through /viewer/api/bench/events/live —
      // this rides over Vite's stable /viewer/api/* proxy to viewer-api
      // and tails the on-disk events.jsonl directly. The WS bus is still
      // the canonical broadcast (lower latency, no polling) but Vite
      // reads .bus-port at startup, so a bench started after Vite booted
      // can't reach the WS without restarting Vite. SSE bypasses that.
      try {
        sse = new EventSource("/viewer/api/bench/events/live");
      } catch (e) {
        setError(`sse-open: ${(e as Error).message}`);
        return;
      }

      sse.onopen = () => setConnected(true);
      sse.onerror = () => {
        // EventSource auto-reconnects; only flip to disconnected so the
        // UI shows a yellow banner.
        setConnected(false);
        setError("sse-error");
      };
      sse.onmessage = (msg) => {
        try {
          const ev = JSON.parse(msg.data) as RunEvent;
          setEvents((prev) => [...prev, ev]);
        } catch {
          // ignore non-JSON keep-alives
        }
      };
    })();

    return () => {
      cancelled = true;
      if (sse) sse.close();
    };
  }, []);

  return { events, connected, error };
}
