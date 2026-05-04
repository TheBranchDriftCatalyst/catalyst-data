/**
 * Shared chunking + color helpers for the audit timeline.
 *
 * `AuditViewer` and `StateInspector` consume the same `RunEvent`
 * shape (from the DuckDB-backed bench audit log) and group events into
 * chunks the same way, so the same renderer works for replay and live
 * streaming.
 */

import type { AuditEvent } from "@/types/benchmark";

export const STAGE_COLORS: Record<string, string> = {
  // v2 (exgraph)
  extract_ner: "bg-blue-500",
  validate_ner: "bg-purple-500",
  repair_ner: "bg-amber-500",
  extract_spo: "bg-cyan-500",
  validate_spo: "bg-violet-500",
  repair_spo: "bg-orange-500",
  // v1 (langgraph)
  extract_mentions: "bg-blue-500",
  validate_mentions: "bg-purple-500",
  repair_mentions: "bg-amber-500",
  extract_propositions: "bg-cyan-500",
  validate_propositions: "bg-violet-500",
  repair_propositions: "bg-orange-500",
  // shared
  persist_artifacts: "bg-emerald-500",
  failure_handler: "bg-red-500",
  // harness/dagster top-level
  run_start: "bg-zinc-500",
  run_end: "bg-zinc-500",
  model_run: "bg-fuchsia-500",
};

/** Lane color by event source (for source-grouped timeline rows). */
export const SOURCE_COLORS: Record<string, string> = {
  harness: "bg-zinc-500",
  exgraph: "bg-cyan-500",
  langgraph: "bg-emerald-500",
  dagster: "bg-violet-500",
};

export function sanitizeName(name: string): string {
  return name.replace(/\//g, "_").replace(/:/g, "_");
}

export interface ChunkGroup {
  index: number;
  events: (AuditEvent & { offsetS: number })[];
  totalDurationS: number;
}

/**
 * Group a flat list of audit events into per-chunk timelines by detecting
 * extract-node restarts.
 */
export function groupByChunk(events: AuditEvent[]): ChunkGroup[] {
  if (events.length === 0) return [];

  const firstTs = new Date(events[0]!.timestamp).getTime();
  const extractNodes = new Set([
    "extract_ner",
    "extract_mentions",
    "extract_spo",
    "extract_propositions",
  ]);

  const groups: ChunkGroup[] = [];
  let current: (AuditEvent & { offsetS: number })[] = [];
  let chunkIdx = 0;

  for (const event of events) {
    const offsetS = (new Date(event.timestamp).getTime() - firstTs) / 1000;
    const enriched = { ...event, offsetS };

    if (
      extractNodes.has(event.node_name) &&
      current.length > 0 &&
      !extractNodes.has(current[current.length - 1]!.node_name)
    ) {
      const totalDur = current.reduce((s, e) => s + (e.duration_s ?? 0), 0);
      groups.push({ index: chunkIdx, events: current, totalDurationS: totalDur });
      current = [];
      chunkIdx++;
    }

    current.push(enriched);
  }

  if (current.length > 0) {
    const totalDur = current.reduce((s, e) => s + (e.duration_s ?? 0), 0);
    groups.push({ index: chunkIdx, events: current, totalDurationS: totalDur });
  }

  return groups;
}
