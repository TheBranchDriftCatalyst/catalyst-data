/**
 * Center column of the StateInspector — vertical timeline of events
 * for one (model × chunk_id) lane, each card expandable to show the
 * full state summary.
 */

import { useState } from "react";

import { STAGE_COLORS } from "@/components/benchmark/auditChunking";
import type { MentionLite, PropositionLite, RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  onHoverError: (idx: number | null) => void;
}

function statusPill(status: string) {
  const palette: Record<string, string> = {
    completed: "bg-emerald-500/20 text-emerald-300",
    valid: "bg-emerald-500/20 text-emerald-300",
    info: "bg-zinc-500/20 text-zinc-300",
    started: "bg-blue-500/20 text-blue-300",
    ambiguous: "bg-amber-500/20 text-amber-300",
    invalid: "bg-red-500/20 text-red-300",
    failed: "bg-red-500/20 text-red-300",
    error: "bg-red-500/20 text-red-300",
  };
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${
        palette[status] ?? "bg-zinc-500/20 text-zinc-300"
      }`}
    >
      {status}
    </span>
  );
}

function ProvenanceBar({ counts }: { counts: Record<string, number> }) {
  const total = (counts.total as number) ?? 0;
  if (total === 0) return null;
  const fields = [
    ["doc", counts.document_id],
    ["chunk", counts.chunk_id],
    ["span", counts.has_span],
    ["model", counts.extraction_model],
    ["spk", counts.speaker_label],
    ["t", counts.temporal_start_ms],
  ] as const;
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {fields.map(([label, n]) => {
        const pct = total > 0 ? (n ?? 0) / total : 0;
        const tone =
          pct === 1
            ? "bg-emerald-500/30 text-emerald-200"
            : pct >= 0.5
              ? "bg-amber-500/30 text-amber-200"
              : "bg-red-500/30 text-red-200";
        return (
          <span
            key={label}
            className={`px-1 py-0.5 rounded text-[9px] font-mono ${tone}`}
            title={`${n ?? 0}/${total}`}
          >
            {label} {n ?? 0}/{total}
          </span>
        );
      })}
    </div>
  );
}

function MentionsRow({ items }: { items: MentionLite[] }) {
  if (!items?.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {items.slice(0, 8).map((m, i) => (
        <span
          key={i}
          className="px-1 py-0.5 rounded text-[10px] font-mono bg-blue-500/20 text-blue-200"
          title={`${m.type ?? m.mention_type ?? ""} · conf=${m.conf ?? m.confidence ?? "?"}`}
        >
          {m.text}
        </span>
      ))}
      {items.length > 8 && (
        <span className="text-[10px] text-zinc-500 self-center">+{items.length - 8} more</span>
      )}
    </div>
  );
}

function PropositionsRow({ items }: { items: PropositionLite[] }) {
  if (!items?.length) return null;
  return (
    <div className="flex flex-col gap-0.5 mt-1">
      {items.slice(0, 5).map((p, i) => (
        <div key={i} className="text-[10px] font-mono text-zinc-400">
          <span className="text-cyan-300">{p.subject}</span>
          <span className="text-zinc-600"> — </span>
          <span className="text-zinc-300">{p.predicate}</span>
          <span className="text-zinc-600"> → </span>
          <span className="text-cyan-300">{p.object}</span>
        </div>
      ))}
      {items.length > 5 && (
        <span className="text-[10px] text-zinc-500">+{items.length - 5} more</span>
      )}
    </div>
  );
}

function EventCard({
  event,
  expanded,
  onToggle,
  onHoverError,
}: {
  event: RunEvent;
  expanded: boolean;
  onToggle: () => void;
  onHoverError: (idx: number | null) => void;
}) {
  const color = STAGE_COLORS[event.node_name] ?? "bg-zinc-500";
  const state = event.state as Record<string, unknown>;
  const details = event.details as Record<string, unknown>;
  const errors = (state.errors as Array<Record<string, unknown>>) ?? [];

  return (
    <div className="border border-white/5 rounded bg-surface-1 mb-2">
      <button
        type="button"
        onClick={onToggle}
        className="w-full px-3 py-2 flex items-center gap-3 text-left hover:bg-white/[0.02]"
      >
        <span className={`w-3 h-3 rounded-sm ${color} flex-shrink-0`} />
        <span className="font-mono text-xs text-zinc-200 flex-shrink-0 w-44 truncate">
          {event.node_name}
        </span>
        {statusPill(event.status)}
        {event.retry_count != null && event.retry_count > 0 && (
          <span className="text-amber-400 font-mono text-[10px]">↻ retry {event.retry_count}</span>
        )}
        {state.candidate_count != null && (
          <span className="text-zinc-500 font-mono text-[10px]">
            {state.candidate_count as number} candidates
          </span>
        )}
        {state.verdict != null && (
          <span className="text-zinc-500 font-mono text-[10px]">
            valid={state.valid_count as number}/
            {(state.valid_count as number) + ((state.invalid_count as number) ?? 0)}
          </span>
        )}
        <span className="ml-auto text-zinc-600 font-mono text-[10px]">
          {new Date(event.ts).toLocaleTimeString()}
        </span>
        <span className="text-zinc-600">{expanded ? "▾" : "▸"}</span>
      </button>

      {expanded && (
        <div className="px-3 pb-2 pt-1 border-t border-white/5">
          {(state.candidate_sample as MentionLite[] | undefined)?.length ? (
            <div className="mt-1">
              <div className="text-[10px] uppercase text-zinc-600 mb-0.5">sample</div>
              {/* Heuristic: propositions have subject/predicate/object */}
              {(state.candidate_sample as Array<Record<string, unknown>>)[0]?.predicate ? (
                <PropositionsRow items={state.candidate_sample as PropositionLite[]} />
              ) : (
                <MentionsRow items={state.candidate_sample as MentionLite[]} />
              )}
            </div>
          ) : null}

          {errors.length > 0 && (
            <div className="mt-2">
              <div className="text-[10px] uppercase text-zinc-600 mb-0.5">
                errors ({errors.length})
              </div>
              <div className="flex flex-col gap-1">
                {errors.map((e, i) => {
                  const ci =
                    typeof e.candidate_index === "number"
                      ? (e.candidate_index as number)
                      : extractIndexFromPath(e.path as string | undefined);
                  return (
                    <div
                      key={i}
                      className="text-[10px] font-mono text-zinc-400 cursor-help"
                      onMouseEnter={() => ci != null && onHoverError(ci)}
                      onMouseLeave={() => onHoverError(null)}
                    >
                      <span className="text-red-300">{e.code as string}</span>
                      <span className="text-zinc-600"> · </span>
                      <span className="text-zinc-500">{e.path as string}</span>
                      <span className="text-zinc-600"> · </span>
                      <span>{e.message as string}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {state.mention_provenance ? (
            <div className="mt-2">
              <div className="text-[10px] uppercase text-zinc-600">mention provenance</div>
              <ProvenanceBar counts={state.mention_provenance as Record<string, number>} />
            </div>
          ) : null}

          {state.proposition_provenance ? (
            <div className="mt-1">
              <div className="text-[10px] uppercase text-zinc-600">proposition provenance</div>
              <ProvenanceBar counts={state.proposition_provenance as Record<string, number>} />
            </div>
          ) : null}

          {event.node_name === "chunk_extracted" && (
            <div className="mt-2 space-y-2">
              <div>
                <div className="text-[10px] uppercase text-zinc-600">
                  final mentions ({(details.mention_count as number) ?? 0})
                </div>
                <MentionsRow items={(details.mentions as MentionLite[]) ?? []} />
              </div>
              <div>
                <div className="text-[10px] uppercase text-zinc-600">
                  final propositions ({(details.proposition_count as number) ?? 0})
                </div>
                <PropositionsRow items={(details.propositions as PropositionLite[]) ?? []} />
              </div>
            </div>
          )}

          {Object.keys(details).length > 0 && event.node_name !== "chunk_extracted" && (
            <details className="mt-2">
              <summary className="text-[10px] uppercase text-zinc-600 cursor-pointer">
                raw details
              </summary>
              <pre className="text-[10px] font-mono text-zinc-400 mt-1 overflow-x-auto">
                {JSON.stringify(details, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function extractIndexFromPath(path: string | undefined): number | null {
  if (!path) return null;
  const m = path.match(/\[(\d+)\]/);
  return m ? parseInt(m[1]!, 10) : null;
}

export function EventStream({ events, onHoverError }: Props) {
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  return (
    <div>
      {events.map((e, i) => {
        const key = `${e.ts}-${i}`;
        return (
          <EventCard
            key={key}
            event={e}
            expanded={expandedKey === key}
            onToggle={() => setExpandedKey((current) => (current === key ? null : key))}
            onHoverError={onHoverError}
          />
        );
      })}
    </div>
  );
}
