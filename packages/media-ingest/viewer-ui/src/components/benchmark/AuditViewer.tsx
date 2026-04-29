import { useState, useEffect, useMemo } from "react";
import type { AuditLog, AuditEvent } from "@/types/benchmark";

// ── Stage colors ─────────────────────────────────────────────────────

const STAGE_COLORS: Record<string, string> = {
  // v2
  extract_ner: "bg-blue-500",
  validate_ner: "bg-purple-500",
  repair_ner: "bg-amber-500",
  extract_spo: "bg-cyan-500",
  validate_spo: "bg-violet-500",
  repair_spo: "bg-orange-500",
  // v1
  extract_mentions: "bg-blue-500",
  validate_mentions: "bg-purple-500",
  repair_mentions: "bg-amber-500",
  extract_propositions: "bg-cyan-500",
  validate_propositions: "bg-violet-500",
  repair_propositions: "bg-orange-500",
  // shared
  persist_artifacts: "bg-emerald-500",
  failure_handler: "bg-red-500",
};

// ── Helpers ──────────────────────────────────────────────────────────

function sanitizeName(name: string): string {
  return name.replace(/\//g, "_").replace(/:/g, "_");
}

async function fetchAuditLog(name: string): Promise<AuditLog | null> {
  try {
    const res = await fetch(`/viewer/audit-logs/${sanitizeName(name)}.json`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

interface ChunkGroup {
  index: number;
  events: (AuditEvent & { offsetS: number })[];
  totalDurationS: number;
}

function groupByChunk(events: AuditEvent[]): ChunkGroup[] {
  if (events.length === 0) return [];

  const firstTs = new Date(events[0]!.timestamp).getTime();

  // Group events into chunks by detecting extract_ner/extract_mentions restarts
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

    // New chunk starts when we see an extract node and current group is non-empty
    // and the previous event wasn't also an extract (handles extract→validate→extract flow)
    if (
      extractNodes.has(event.node_name) &&
      current.length > 0 &&
      !extractNodes.has(current[current.length - 1]!.node_name)
    ) {
      const totalDur = current.reduce((s, e) => s + e.duration_s, 0);
      groups.push({ index: chunkIdx, events: current, totalDurationS: totalDur });
      current = [];
      chunkIdx++;
    }

    current.push(enriched);
  }

  if (current.length > 0) {
    const totalDur = current.reduce((s, e) => s + e.duration_s, 0);
    groups.push({ index: chunkIdx, events: current, totalDurationS: totalDur });
  }

  return groups;
}

// ── Gantt Chart ──────────────────────────────────────────────────────

function GanttChart({ log, maxDuration }: { log: AuditLog; maxDuration: number }) {
  const chunks = useMemo(() => groupByChunk(log.audit_events), [log]);
  const [selectedEvent, setSelectedEvent] = useState<
    (AuditEvent & { offsetS: number; chunkIndex: number }) | null
  >(null);

  const totalDuration =
    maxDuration ||
    Math.max(
      ...log.audit_events.map((e) => {
        const firstTs = new Date(log.audit_events[0]!.timestamp).getTime();
        return (new Date(e.timestamp).getTime() - firstTs) / 1000 + e.duration_s;
      }),
      1,
    );

  // Time axis labels
  const ticks = [];
  const tickInterval = totalDuration > 60 ? 30 : totalDuration > 10 ? 5 : 1;
  for (let t = 0; t <= totalDuration; t += tickInterval) {
    ticks.push(t);
  }

  return (
    <div>
      {/* Summary stats */}
      <div className="flex gap-4 mb-3 text-[10px] font-mono text-zinc-500">
        <span>
          Duration: <span className="text-zinc-300">{log.stats.duration_s?.toFixed(1)}s</span>
        </span>
        <span>
          Events: <span className="text-zinc-300">{log.event_count}</span>
        </span>
        <span>
          Mentions: <span className="text-zinc-300">{log.stats.mention_count}</span>
        </span>
        <span>
          Assertions: <span className="text-zinc-300">{log.stats.assertion_count}</span>
        </span>
        <span>
          Retries:{" "}
          <span
            className={
              log.stats.mention_retries + log.stats.proposition_retries > 0
                ? "text-amber-400"
                : "text-zinc-300"
            }
          >
            {log.stats.mention_retries + log.stats.proposition_retries}
          </span>
        </span>
      </div>

      {/* Time axis */}
      <div className="relative ml-16 mr-4 h-4 border-b border-white/10 mb-1">
        {ticks.map((t) => (
          <div
            key={t}
            className="absolute top-0 text-[8px] text-zinc-600 font-mono"
            style={{ left: `${(t / totalDuration) * 100}%`, transform: "translateX(-50%)" }}
          >
            {t}s
          </div>
        ))}
      </div>

      {/* Chunk rows */}
      <div className="space-y-0.5">
        {chunks.map((chunk) => (
          <div key={chunk.index} className="flex items-center group">
            {/* Chunk label */}
            <div className="w-16 flex-shrink-0 text-[9px] text-zinc-600 font-mono text-right pr-2">
              chunk {chunk.index + 1}
            </div>

            {/* Gantt bars */}
            <div className="flex-1 relative h-6 bg-white/[0.02] rounded-sm">
              {chunk.events.map((event, i) => {
                const startPct = (event.offsetS / totalDuration) * 100;
                const widthPct = Math.max((event.duration_s / totalDuration) * 100, 0.3);
                const color = STAGE_COLORS[event.node_name] || "bg-zinc-500";
                const isSelected = selectedEvent === event;

                return (
                  <div
                    key={i}
                    className={`absolute top-0.5 h-5 ${color} rounded-sm cursor-pointer transition-opacity ${isSelected ? "opacity-100 ring-1 ring-white/40" : "opacity-70 hover:opacity-100"}`}
                    style={{
                      left: `${startPct}%`,
                      width: `${widthPct}%`,
                      minWidth: "3px",
                    }}
                    title={`${event.node_name} (${event.status})`}
                    onClick={() =>
                      setSelectedEvent(isSelected ? null : { ...event, chunkIndex: chunk.index })
                    }
                  >
                    {event.status === "ambiguous" && (
                      <span className="absolute -top-1 -right-1 w-2 h-2 bg-amber-400 rounded-full" />
                    )}
                    {(event.status === "error" ||
                      event.status === "failed" ||
                      event.status === "invalid") && (
                      <span className="absolute -top-1 -right-1 w-2 h-2 bg-red-500 rounded-full" />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mt-3 text-[9px] font-mono">
        {Object.entries(STAGE_COLORS)
          .filter(([k]) => log.audit_events.some((e) => e.node_name === k))
          .map(([name, color]) => (
            <div key={name} className="flex items-center gap-1">
              <span className={`w-3 h-2 ${color} rounded-sm inline-block`} />
              <span className="text-zinc-500">{name.replace(/_/g, " ")}</span>
            </div>
          ))}
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 bg-amber-400 rounded-full inline-block" />
          <span className="text-zinc-500">ambiguous</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 bg-red-500 rounded-full inline-block" />
          <span className="text-zinc-500">error</span>
        </div>
      </div>

      {/* Detail panel — shown below chart when an event is clicked */}
      {selectedEvent && (
        <div className="mt-3 bg-surface-1 border border-white/10 rounded-lg p-4 font-mono text-xs">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <span
                className={`w-3 h-3 rounded-sm inline-block ${STAGE_COLORS[selectedEvent.node_name] || "bg-zinc-500"}`}
              />
              <span className="text-zinc-100 text-sm">
                {selectedEvent.node_name.replace(/_/g, " ")}
              </span>
              <span
                className={`px-1.5 py-0.5 rounded text-[10px] ${
                  selectedEvent.status === "completed" || selectedEvent.status === "valid"
                    ? "bg-emerald-500/20 text-emerald-300"
                    : selectedEvent.status === "ambiguous"
                      ? "bg-amber-500/20 text-amber-300"
                      : "bg-red-500/20 text-red-300"
                }`}
              >
                {selectedEvent.status}
              </span>
            </div>
            <button
              onClick={() => setSelectedEvent(null)}
              className="text-zinc-500 hover:text-zinc-300 text-sm"
            >
              ✕
            </button>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <div>
              <div className="text-zinc-600 text-[10px] uppercase">Chunk</div>
              <div className="text-zinc-300">{selectedEvent.chunkIndex + 1}</div>
            </div>
            <div>
              <div className="text-zinc-600 text-[10px] uppercase">Duration</div>
              <div className="text-zinc-300">{selectedEvent.duration_s.toFixed(3)}s</div>
            </div>
            <div>
              <div className="text-zinc-600 text-[10px] uppercase">Offset</div>
              <div className="text-zinc-300">{selectedEvent.offsetS.toFixed(1)}s from start</div>
            </div>
            {selectedEvent.details.candidate_count != null && (
              <div>
                <div className="text-zinc-600 text-[10px] uppercase">Candidates</div>
                <div className="text-zinc-300">{selectedEvent.details.candidate_count}</div>
              </div>
            )}
            {selectedEvent.details.verdict && (
              <div>
                <div className="text-zinc-600 text-[10px] uppercase">Verdict</div>
                <div className="text-zinc-300">{selectedEvent.details.verdict}</div>
              </div>
            )}
          </div>

          {selectedEvent.details.errors && selectedEvent.details.errors.length > 0 && (
            <div>
              <div className="text-zinc-600 text-[10px] uppercase mb-1">
                Validation Errors ({selectedEvent.details.errors.length})
              </div>
              <div className="max-h-[200px] overflow-y-auto space-y-1 bg-surface-0 rounded p-2">
                {selectedEvent.details.errors.map((err, j) => (
                  <div key={j} className="flex gap-2">
                    <span className="text-red-400 flex-shrink-0">[{err.code}]</span>
                    <span className="text-zinc-400">{err.message}</span>
                    {err.path && <span className="text-zinc-600 flex-shrink-0">{err.path}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {(!selectedEvent.details.errors || selectedEvent.details.errors.length === 0) &&
            selectedEvent.status !== "ambiguous" && (
              <div className="text-zinc-600 text-[10px]">No validation errors.</div>
            )}
        </div>
      )}
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────

export function AuditViewer({ modelNames }: { modelNames: string[] }) {
  const [modelA, setModelA] = useState<string>("");
  const [modelB, setModelB] = useState<string>("");
  const [logA, setLogA] = useState<AuditLog | null>(null);
  const [logB, setLogB] = useState<AuditLog | null>(null);
  const [loadingA, setLoadingA] = useState(false);
  const [loadingB, setLoadingB] = useState(false);
  const [errorA, setErrorA] = useState<string | null>(null);
  const [errorB, setErrorB] = useState<string | null>(null);

  useEffect(() => {
    if (!modelA) {
      setLogA(null);
      return;
    }
    setLoadingA(true);
    setErrorA(null);
    fetchAuditLog(modelA).then((log) => {
      if (log) setLogA(log);
      else setErrorA("No audit log (run with --audit-log)");
      setLoadingA(false);
    });
  }, [modelA]);

  useEffect(() => {
    if (!modelB) {
      setLogB(null);
      return;
    }
    setLoadingB(true);
    setErrorB(null);
    fetchAuditLog(modelB).then((log) => {
      if (log) setLogB(log);
      else setErrorB("No audit log (run with --audit-log)");
      setLoadingB(false);
    });
  }, [modelB]);

  // Shared time scale for comparison
  const maxDuration = useMemo(() => {
    const durA = logA ? logA.stats.duration_s : 0;
    const durB = logB ? logB.stats.duration_s : 0;
    return Math.max(durA, durB, 1);
  }, [logA, logB]);

  return (
    <div className="space-y-4">
      {/* Model selectors */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="text-[10px] text-zinc-500 font-mono">Model A:</label>
          <select
            value={modelA}
            onChange={(e) => setModelA(e.target.value)}
            className="bg-surface-1 border border-white/10 rounded px-2 py-1 text-xs font-mono text-zinc-200"
          >
            <option value="">Select model...</option>
            {modelNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-[10px] text-zinc-500 font-mono">Model B (compare):</label>
          <select
            value={modelB}
            onChange={(e) => setModelB(e.target.value)}
            className="bg-surface-1 border border-white/10 rounded px-2 py-1 text-xs font-mono text-zinc-200"
          >
            <option value="">None</option>
            {modelNames
              .filter((n) => n !== modelA)
              .map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
          </select>
        </div>
      </div>

      {/* Content */}
      {!modelA && (
        <div className="text-zinc-500 text-sm py-8 text-center">
          Select a model to view its extraction pipeline audit trail.
        </div>
      )}

      {loadingA && <div className="text-zinc-500 text-xs font-mono">Loading audit log...</div>}
      {errorA && <div className="text-amber-400 text-xs font-mono">{errorA}</div>}

      {logA && (
        <div>
          <h3 className="text-xs font-mono text-zinc-400 mb-2">
            {logA.name}
            <span className="text-zinc-600 ml-2">({logA.tags.join(", ")})</span>
          </h3>
          <GanttChart log={logA} maxDuration={logB ? maxDuration : 0} />
        </div>
      )}

      {logB && logA && (
        <div className="border-t border-white/5 pt-4">
          {/* Comparison delta */}
          <div className="flex gap-4 mb-2 text-[10px] font-mono">
            <span className="text-zinc-500">
              Δ Duration:
              <span
                className={
                  logB.stats.duration_s < logA.stats.duration_s
                    ? "text-emerald-400"
                    : "text-red-400"
                }
              >
                {" "}
                {logB.stats.duration_s < logA.stats.duration_s ? "" : "+"}
                {(logB.stats.duration_s - logA.stats.duration_s).toFixed(1)}s
              </span>
            </span>
            <span className="text-zinc-500">
              Δ Events:
              <span className="text-zinc-300"> {logB.event_count - logA.event_count}</span>
            </span>
            <span className="text-zinc-500">
              Δ Mentions:
              <span className="text-zinc-300">
                {" "}
                {logB.stats.mention_count - logA.stats.mention_count}
              </span>
            </span>
          </div>

          <h3 className="text-xs font-mono text-zinc-400 mb-2">
            {logB.name}
            <span className="text-zinc-600 ml-2">({logB.tags.join(", ")})</span>
          </h3>
          <GanttChart log={logB} maxDuration={maxDuration} />
        </div>
      )}

      {loadingB && <div className="text-zinc-500 text-xs font-mono">Loading audit log...</div>}
      {errorB && <div className="text-amber-400 text-xs font-mono">{errorB}</div>}
    </div>
  );
}
