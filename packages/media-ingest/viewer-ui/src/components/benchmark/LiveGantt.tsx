/**
 * Live Gantt — renders the unified `events.jsonl` stream as a swimlane
 * per (source × model). Subscribes to the harness's run-bus over WS for
 * real-time updates; when the bus is offline, replays the static
 * `events.jsonl` from disk. Reuses `STAGE_COLORS` and the same chunk
 * grouping as `AuditViewer` so the live view and the post-hoc view look
 * identical.
 */

import { useMemo } from "react";

import { useRunStream } from "@/hooks/useRunStream";
import type { RunEvent } from "@/types/benchmark";
import { SOURCE_COLORS, STAGE_COLORS } from "./auditChunking";

interface Lane {
  key: string;
  source: string;
  model: string | null;
  events: RunEvent[];
}

function groupLanes(events: RunEvent[]): Lane[] {
  const lanes = new Map<string, Lane>();
  for (const ev of events) {
    const key = `${ev.source}::${ev.model ?? "—"}`;
    const lane = lanes.get(key);
    if (lane) {
      lane.events.push(ev);
    } else {
      lanes.set(key, { key, source: ev.source, model: ev.model, events: [ev] });
    }
  }
  // Stable order: harness first, then dagster, then exgraph, then langgraph
  const order = ["harness", "dagster", "exgraph", "langgraph"];
  return Array.from(lanes.values()).sort((a, b) => {
    const ai = order.indexOf(a.source);
    const bi = order.indexOf(b.source);
    if (ai !== bi) return ai - bi;
    return (a.model ?? "").localeCompare(b.model ?? "");
  });
}

export function LiveGantt() {
  const { events, connected, error } = useRunStream();
  const lanes = useMemo(() => groupLanes(events), [events]);

  const { firstTs, totalDuration } = useMemo(() => {
    if (events.length === 0) return { firstTs: 0, totalDuration: 1 };
    const first = new Date(events[0]!.ts).getTime();
    const last = new Date(events[events.length - 1]!.ts).getTime();
    return { firstTs: first, totalDuration: Math.max((last - first) / 1000, 1) };
  }, [events]);

  if (events.length === 0) {
    return (
      <div className="rounded-lg border border-white/10 bg-surface-1 p-4 font-mono text-xs text-zinc-500">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-zinc-300">live timeline</span>
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] ${
              connected
                ? "bg-emerald-500/20 text-emerald-300"
                : error === "offline (replay)"
                  ? "bg-zinc-500/20 text-zinc-400"
                  : "bg-amber-500/20 text-amber-300"
            }`}
          >
            {connected ? "live" : (error ?? "connecting…")}
          </span>
        </div>
        <div className="text-zinc-600">No events yet — start a benchmark run.</div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-white/10 bg-surface-1 p-4">
      <div className="mb-3 flex items-center gap-2 font-mono text-[11px]">
        <span className="text-zinc-300">live timeline</span>
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] ${
            connected
              ? "bg-emerald-500/20 text-emerald-300"
              : error === "offline (replay)"
                ? "bg-zinc-500/20 text-zinc-400"
                : "bg-amber-500/20 text-amber-300"
          }`}
        >
          {connected ? "live" : (error ?? "connecting…")}
        </span>
        <span className="text-zinc-500">{events.length} events</span>
        <span className="text-zinc-500">·</span>
        <span className="text-zinc-500">{lanes.length} lanes</span>
        <span className="text-zinc-500">·</span>
        <span className="text-zinc-500">{totalDuration.toFixed(1)}s</span>
      </div>

      <div className="space-y-1">
        {lanes.map((lane) => (
          <div key={lane.key} className="flex items-center group">
            <div className="w-40 flex-shrink-0 text-[11px] font-mono text-right pr-2">
              <span
                className={`inline-block w-2 h-2 rounded-sm mr-1 ${
                  SOURCE_COLORS[lane.source] ?? "bg-zinc-500"
                }`}
              />
              <span className="text-zinc-500">{lane.source}</span>
              {lane.model && lane.model !== "—" && (
                <>
                  <span className="text-zinc-700"> · </span>
                  <span className="text-zinc-300">{lane.model}</span>
                </>
              )}
            </div>

            <div className="flex-1 relative h-6 bg-white/[0.02] rounded-sm">
              {lane.events.map((ev, i) => {
                const tStart = (new Date(ev.ts).getTime() - firstTs) / 1000;
                const tEnd =
                  i + 1 < lane.events.length
                    ? (new Date(lane.events[i + 1]!.ts).getTime() - firstTs) / 1000
                    : tStart + 0.5;
                const startPct = (tStart / totalDuration) * 100;
                const widthPct = Math.max(((tEnd - tStart) / totalDuration) * 100, 0.3);
                const color =
                  STAGE_COLORS[ev.node_name] ?? SOURCE_COLORS[ev.source] ?? "bg-zinc-500";
                const isError =
                  ev.status === "error" || ev.status === "failed" || ev.status === "invalid";

                // Deep-link to the StateInspector when the event has
                // both a model and a chunk_id.
                const link =
                  ev.model && ev.chunk_id
                    ? `/viewer/benchmarks/state?model=${encodeURIComponent(ev.model)}&chunk_id=${encodeURIComponent(ev.chunk_id)}`
                    : null;
                const tip = `${ev.source} · ${ev.node_name} · ${ev.status}${ev.model ? ` · ${ev.model}` : ""}${ev.chunk_id ? ` · ${ev.chunk_id}` : ""}`;

                const bar = (
                  <div
                    className={`absolute top-0.5 h-5 ${color} rounded-sm opacity-70 hover:opacity-100 ${link ? "cursor-pointer" : ""}`}
                    style={{
                      left: `${startPct}%`,
                      width: `${widthPct}%`,
                      minWidth: "3px",
                    }}
                    title={tip}
                  >
                    {isError && (
                      <span className="absolute -top-1 -right-1 w-2 h-2 bg-red-500 rounded-full" />
                    )}
                  </div>
                );
                return link ? (
                  <a key={`${ev.ts}-${i}`} href={link}>
                    {bar}
                  </a>
                ) : (
                  <span key={`${ev.ts}-${i}`}>{bar}</span>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap gap-3 text-[11px] font-mono">
        {Object.entries(SOURCE_COLORS).map(([source, color]) => (
          <div key={source} className="flex items-center gap-1">
            <span className={`w-3 h-2 ${color} rounded-sm inline-block`} />
            <span className="text-zinc-500">{source}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
