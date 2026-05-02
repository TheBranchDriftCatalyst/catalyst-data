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
    let ws: WebSocket | null = null;

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

      try {
        // Same-origin via Vite's /viewer/bus proxy → 127.0.0.1:<bus_port>/stream
        const wsScheme = window.location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${wsScheme}://${window.location.host}/viewer/bus/stream`);
      } catch (e) {
        setError(`ws-open: ${(e as Error).message}`);
        return;
      }

      ws.onopen = () => setConnected(true);
      ws.onclose = () => setConnected(false);
      ws.onerror = () => {
        setError("ws-error");
        setConnected(false);
      };
      ws.onmessage = (msg) => {
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
      if (ws) ws.close();
    };
  }, []);

  return { events, connected, error };
}
