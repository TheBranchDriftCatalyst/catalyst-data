/**
 * Center pane of the StateInspector — horizontal strip of chunk cards
 * for one (model × doc) pair, with a left→right input → stages → output
 * detail pane below the strip when a chunk is selected.
 *
 * Goal: spot chunker problems at a glance (oversize, undersize, off-by-one
 * boundary, missing speaker carry-over) by seeing all chunks of a doc in
 * order, with their pipeline status badges and mention counts side-by-side.
 *
 * Card layout:
 *   #idx  speaker/preview   mentions   time/char range   stage ticks
 *
 * Stage ticks come from PIPELINE_NODES — one dot per stage, color = last
 * status. Retry icons (↻) appear when retry_count > 0; error icons (!)
 * appear when any stage hit error/failed.
 */

import { useMemo, useState } from "react";

import type { MentionLite, PropositionLite, RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[]; // already filtered to (model, doc)
  allEvents: RunEvent[]; // unfiltered — used for chunk_loaded lookup
  model: string;
  docId: string;
  selectedChunk: string | null;
  onSelectChunk: (chunkId: string) => void;
}

const PIPELINE_STAGES = [
  "extract_ner",
  "validate_ner",
  "repair_ner",
  "extract_spo",
  "validate_spo",
  "repair_spo",
  "persist_artifacts",
] as const;

type StageName = (typeof PIPELINE_STAGES)[number];

interface StageRun {
  status: string;
  durationS: number;
  invocations: number;
  /** Every event we saw for this stage on this chunk, in arrival order.
   *  Lets the OUTPUT pane render full retry chains: extract → validate
   *  → repair → validate-again, with each iteration's candidates +
   *  errors visible. */
  events: RunEvent[];
}

interface ChunkCard {
  chunkId: string;
  index: number | null; // from chunk_loaded.details.chunk_index
  totalChunks: number | null;
  speakerLabel: string | null;
  domain: string | null;
  textPreview: string;
  charCount: number | null;
  charOffset: number | null;
  temporalStartMs: number | null;
  temporalEndMs: number | null;
  mentionCount: number | null;
  propositionCount: number | null;
  hasError: boolean;
  retries: number;
  stages: Map<StageName, StageRun>;
  mentions: MentionLite[];
  propositions: PropositionLite[];
  text: string | null; // from chunk_loaded
}

function statusDot(status: string | undefined) {
  if (!status) return "bg-zinc-700";
  if (status === "error" || status === "failed" || status === "invalid") return "bg-red-400";
  if (status === "ambiguous") return "bg-amber-400";
  if (status === "completed" || status === "valid") return "bg-emerald-400";
  if (status === "started") return "bg-blue-400";
  return "bg-zinc-500";
}

function fmtSec(ms: number | null) {
  if (ms == null) return "—";
  return `${(ms / 1000).toFixed(0)}s`;
}

export function ChunkTimeline({
  events,
  allEvents,
  model,
  docId,
  selectedChunk,
  onSelectChunk,
}: Props) {
  const cards = useMemo(() => {
    const map = new Map<string, ChunkCard>();
    // First pass: chunk_loaded events (model:null, scoped by chunk_id) for source text + chunk metadata
    for (const e of allEvents) {
      if (e.node_name !== "chunk_loaded" || !e.chunk_id) continue;
      const eDocId = e.doc_id ?? null;
      if (eDocId && eDocId !== docId) continue;
      const d = (e.details ?? {}) as Record<string, unknown>;
      const cm = (d.chunk_metadata ?? {}) as Record<string, unknown>;
      let card = map.get(e.chunk_id);
      if (!card) {
        card = {
          chunkId: e.chunk_id,
          index: null,
          totalChunks: null,
          speakerLabel: null,
          domain: null,
          textPreview: "",
          charCount: null,
          charOffset: null,
          temporalStartMs: null,
          temporalEndMs: null,
          mentionCount: null,
          propositionCount: null,
          hasError: false,
          retries: 0,
          stages: new Map(),
          mentions: [],
          propositions: [],
          text: null,
        };
        map.set(e.chunk_id, card);
      }
      card.text = (d.text as string) ?? card.text;
      card.textPreview = (card.text ?? "").slice(0, 80);
      card.charCount = (d.char_count as number) ?? card.charCount;
      card.domain = (d.domain as string) ?? card.domain;
      card.speakerLabel = (d.speaker_label as string) ?? card.speakerLabel;
      card.temporalStartMs = (d.temporal_start_ms as number) ?? card.temporalStartMs;
      card.temporalEndMs = (d.temporal_end_ms as number) ?? card.temporalEndMs;
      card.index = (d.chunk_index as number) ?? card.index;
      card.totalChunks = (d.total_chunks as number) ?? card.totalChunks;
      card.charOffset = (cm.chunk_char_offset as number) ?? card.charOffset;
    }
    // Second pass: per-(model, doc) events. Build stage rollup + final extraction output.
    for (const e of events) {
      if (!e.chunk_id) continue;
      let card = map.get(e.chunk_id);
      if (!card) {
        // Defensive: if chunk_loaded was missed, still surface the card.
        card = {
          chunkId: e.chunk_id,
          index: null,
          totalChunks: null,
          speakerLabel: null,
          domain: null,
          textPreview: "",
          charCount: null,
          charOffset: null,
          temporalStartMs: null,
          temporalEndMs: null,
          mentionCount: null,
          propositionCount: null,
          hasError: false,
          retries: 0,
          stages: new Map(),
          mentions: [],
          propositions: [],
          text: null,
        };
        map.set(e.chunk_id, card);
      }
      if (e.status === "error" || e.status === "failed") card.hasError = true;
      if (e.retry_count != null && e.retry_count > card.retries) card.retries = e.retry_count;

      if (e.node_name === "chunk_extracted") {
        const d = (e.details ?? {}) as Record<string, unknown>;
        card.mentionCount = (d.mention_count as number) ?? null;
        card.propositionCount = (d.proposition_count as number) ?? null;
        card.mentions = (d.mentions as MentionLite[]) ?? [];
        card.propositions = (d.propositions as PropositionLite[]) ?? [];
      }

      if ((PIPELINE_STAGES as readonly string[]).includes(e.node_name)) {
        const node = e.node_name as StageName;
        let st = card.stages.get(node);
        if (!st) {
          st = { status: e.status, durationS: 0, invocations: 0, events: [] };
          card.stages.set(node, st);
        }
        st.invocations += 1;
        st.status = e.status;
        st.events.push(e);
        const d = (e.details ?? {}) as Record<string, unknown>;
        if (typeof d.duration_s === "number") st.durationS += d.duration_s;
      }
    }
    // Sort cards by (index ?? lex chunk_id)
    return [...map.values()].sort((a, b) => {
      if (a.index != null && b.index != null) return a.index - b.index;
      return a.chunkId.localeCompare(b.chunkId, undefined, { numeric: true });
    });
  }, [events, allEvents, docId]);

  // Header — pull chunking strategy from the first card that has chunk_metadata.
  const header = useMemo(() => {
    let strategy: string | null = null;
    let chunkSize: number | null = null;
    let chunkOverlap: number | null = null;
    for (const e of allEvents) {
      if (e.node_name !== "chunk_loaded" || !e.chunk_id) continue;
      if (e.doc_id && e.doc_id !== docId) continue;
      const d = (e.details ?? {}) as Record<string, unknown>;
      const cm = (d.chunk_metadata ?? {}) as Record<string, unknown>;
      strategy = (cm.strategy as string) ?? strategy;
      chunkSize = (cm.chunk_size as number) ?? chunkSize;
      chunkOverlap = (cm.chunk_overlap as number) ?? chunkOverlap;
      if (strategy && chunkSize) break;
    }
    const totalMentions = cards.reduce((s, c) => s + (c.mentionCount ?? 0), 0);
    const totalProps = cards.reduce((s, c) => s + (c.propositionCount ?? 0), 0);
    return { strategy, chunkSize, chunkOverlap, totalMentions, totalProps };
  }, [allEvents, docId, cards]);

  const selected = cards.find((c) => c.chunkId === selectedChunk) ?? null;

  if (cards.length === 0) {
    return (
      <div className="p-6 font-mono text-xs text-zinc-500">
        No chunks for this doc yet — waiting for `chunk_loaded` events.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Doc header */}
      <div className="px-4 py-2 border-b border-white/5 font-mono text-[11px] flex items-center gap-3 flex-wrap">
        <span className="text-zinc-300 font-semibold">{docId}</span>
        <span className="text-zinc-600">·</span>
        <span className="text-zinc-400">
          {cards.length} chunk{cards.length === 1 ? "" : "s"}
        </span>
        <span className="text-zinc-600">·</span>
        <span className="text-blue-300">{header.totalMentions} mentions</span>
        <span className="text-zinc-600">·</span>
        <span className="text-cyan-300">{header.totalProps} props</span>
        {header.strategy && (
          <>
            <span className="text-zinc-700">|</span>
            <span className="text-zinc-500">
              strategy <span className="text-zinc-300">{header.strategy}</span>
            </span>
          </>
        )}
        {header.chunkSize != null && (
          <span className="text-zinc-500">
            size <span className="text-zinc-300">{header.chunkSize}</span>
          </span>
        )}
        {header.chunkOverlap != null && (
          <span className="text-zinc-500">
            overlap <span className="text-zinc-300">{header.chunkOverlap}</span>
          </span>
        )}
        <span className="ml-auto text-zinc-600 truncate max-w-xs">model: {model}</span>
      </div>

      {/* Horizontal scrollable strip */}
      <div className="overflow-x-auto overflow-y-hidden flex-shrink-0 border-b border-white/5">
        <div className="flex gap-2 p-3 min-w-max">
          {cards.map((c) => {
            const isSel = c.chunkId === selectedChunk;
            return (
              <button
                key={c.chunkId}
                type="button"
                onClick={() => onSelectChunk(c.chunkId)}
                className={`flex-shrink-0 w-44 text-left rounded border font-mono text-[10px] p-2 transition-colors ${
                  isSel
                    ? "bg-cyan-500/10 border-cyan-500/40 text-cyan-100"
                    : "bg-surface-1 border-white/10 text-zinc-400 hover:bg-white/[0.04]"
                }`}
                title={c.chunkId}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-zinc-200 font-semibold">
                    #{c.index ?? "?"}
                    {c.totalChunks != null && (
                      <span className="text-zinc-600">/{c.totalChunks}</span>
                    )}
                  </span>
                  <div className="flex items-center gap-1">
                    {c.retries > 0 && <span className="text-amber-400">↻{c.retries}</span>}
                    {c.hasError && <span className="text-red-400">!</span>}
                  </div>
                </div>
                <div className="text-zinc-500 truncate text-[9.5px] mb-1">
                  {c.speakerLabel ? (
                    <>
                      <span className="text-blue-300">{c.speakerLabel}</span>{" "}
                      {c.textPreview.slice(0, 50)}
                    </>
                  ) : (
                    c.textPreview.slice(0, 70) || "(no preview)"
                  )}
                </div>
                <div className="flex items-center justify-between text-zinc-600 text-[9.5px] mb-1.5">
                  <span>
                    {c.temporalStartMs != null && c.temporalEndMs != null
                      ? `${fmtSec(c.temporalStartMs)}–${fmtSec(c.temporalEndMs)}`
                      : c.charCount != null
                        ? `${c.charCount}ch`
                        : ""}
                  </span>
                  <span>
                    {c.mentionCount != null ? (
                      <>
                        <span className="text-blue-300">{c.mentionCount}m</span>
                        {c.propositionCount != null && c.propositionCount > 0 && (
                          <>
                            <span>·</span>
                            <span className="text-cyan-300">{c.propositionCount}p</span>
                          </>
                        )}
                      </>
                    ) : (
                      "—"
                    )}
                  </span>
                </div>
                {/* Stage tick row */}
                <div className="flex items-center gap-0.5">
                  {PIPELINE_STAGES.map((s) => {
                    const st = c.stages.get(s);
                    return (
                      <span
                        key={s}
                        className={`w-1.5 h-1.5 rounded-sm ${statusDot(st?.status)}`}
                        title={
                          st
                            ? `${s}: ${st.status}${st.invocations > 1 ? ` ×${st.invocations}` : ""} · ${st.durationS.toFixed(2)}s`
                            : `${s}: skipped`
                        }
                      />
                    );
                  })}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Selected chunk detail — input → stages → output, left to right */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {selected ? (
          <ChunkFlow card={selected} />
        ) : (
          <div className="p-6 font-mono text-xs text-zinc-500">
            Click a chunk in the strip above to inspect input → stages → output.
          </div>
        )}
      </div>
    </div>
  );
}

function ChunkFlow({ card }: { card: ChunkCard }) {
  const text = card.text ?? "";
  // selectedStage = null → final accepted output (chunk_extracted)
  // selectedStage = "extract_ner" | … → that stage's events (candidates,
  //   verdicts, repair attempts) so the user can see WHY repair retried.
  const [selectedStage, setSelectedStage] = useState<StageName | null>(null);
  return (
    <div className="grid grid-cols-[1fr_220px_1fr] gap-3 p-4 min-h-full">
      {/* INPUT */}
      <section className="flex flex-col min-w-0">
        <div className="text-[10px] uppercase text-zinc-600 mb-1">input · chunk text</div>
        <div className="flex-1 rounded border border-white/5 bg-surface-0 p-3 text-[11px] font-mono text-zinc-300 whitespace-pre-wrap break-words leading-relaxed overflow-y-auto">
          {text || <span className="text-zinc-600">(no text yet)</span>}
        </div>
        <div className="mt-1 text-[10px] text-zinc-600 font-mono">
          {card.charCount ?? text.length} chars
          {card.charOffset != null && (
            <>
              {" · "}offset {card.charOffset}
              {card.charCount != null && <> – {card.charOffset + card.charCount}</>}
            </>
          )}
        </div>
      </section>

      {/* STAGES — vertical column of clickable stage badges. The
          "final" entry at the top is the default selection; click any
          stage to swap the OUTPUT pane to that stage's events. */}
      <section className="flex flex-col">
        <div className="text-[10px] uppercase text-zinc-600 mb-1">pipeline</div>
        <div className="flex-1 rounded border border-white/5 bg-surface-0 p-2 space-y-1">
          <button
            type="button"
            onClick={() => setSelectedStage(null)}
            className={`w-full flex items-center gap-2 px-2 py-1 rounded font-mono text-[10px] text-left ${
              selectedStage === null
                ? "bg-cyan-500/15 text-cyan-200 ring-1 ring-cyan-500/40"
                : "bg-white/[0.02] hover:bg-white/[0.05] text-zinc-300"
            }`}
          >
            <span className="w-2 h-2 rounded-sm bg-cyan-400" />
            <span className="flex-1 truncate">final</span>
            <span className="text-zinc-600">accepted</span>
          </button>
          {PIPELINE_STAGES.map((s) => {
            const st = card.stages.get(s);
            const isSel = selectedStage === s;
            const isClickable = !!st;
            return (
              <button
                key={s}
                type="button"
                disabled={!isClickable}
                onClick={() => setSelectedStage(s)}
                className={`w-full flex items-center gap-2 px-2 py-1 rounded font-mono text-[10px] text-left transition-colors ${
                  isSel
                    ? "bg-cyan-500/15 text-cyan-200 ring-1 ring-cyan-500/40"
                    : isClickable
                      ? "bg-white/[0.02] hover:bg-white/[0.05] text-zinc-300"
                      : "bg-white/[0.01] text-zinc-600 cursor-default"
                }`}
              >
                <span className={`w-2 h-2 rounded-sm ${statusDot(st?.status)}`} />
                <span className="flex-1 truncate">{s}</span>
                {st && st.invocations > 1 && (
                  <span className="text-amber-400">×{st.invocations}</span>
                )}
                <span className="text-zinc-600">
                  {st && st.durationS > 0 ? `${(st.durationS * 1000).toFixed(0)}ms` : ""}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* OUTPUT — flips between final accepted output and the selected
          stage's per-invocation events. */}
      <section className="flex flex-col min-w-0">
        {selectedStage === null ? (
          <FinalOutput card={card} />
        ) : (
          <StageOutput stage={selectedStage} run={card.stages.get(selectedStage)} />
        )}
      </section>
    </div>
  );
}

function FinalOutput({ card }: { card: ChunkCard }) {
  return (
    <>
      <div className="text-[10px] uppercase text-zinc-600 mb-1">
        output · final · {card.mentionCount ?? 0} mentions · {card.propositionCount ?? 0} props
      </div>
      <div className="flex-1 rounded border border-white/5 bg-surface-0 p-3 overflow-y-auto space-y-2">
        {card.mentions.length > 0 && (
          <div>
            <div className="text-[10px] uppercase text-zinc-600 mb-1">mentions</div>
            <div className="flex flex-wrap gap-1">
              {card.mentions.map((m, i) => (
                <span
                  key={i}
                  className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-blue-500/15 text-blue-200 border border-blue-500/30"
                  title={`${m.type ?? m.mention_type ?? "?"} · [${m.span_start ?? "?"}, ${m.span_end ?? "?"})`}
                >
                  {m.text}
                </span>
              ))}
            </div>
          </div>
        )}
        {card.propositions.length > 0 && (
          <div>
            <div className="text-[10px] uppercase text-zinc-600 mb-1">propositions</div>
            <div className="space-y-1">
              {card.propositions.map((p, i) => (
                <div
                  key={i}
                  className="text-[10px] font-mono text-zinc-300 bg-white/[0.02] rounded px-2 py-1"
                >
                  <span className="text-cyan-300">{p.subject}</span>
                  <span className="text-zinc-600"> — </span>
                  <span className="text-zinc-200">{p.predicate}</span>
                  <span className="text-zinc-600"> → </span>
                  <span className="text-cyan-300">{p.object}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {card.mentions.length === 0 && card.propositions.length === 0 && (
          <div className="text-[10px] font-mono text-zinc-600">(no extractions yet)</div>
        )}
      </div>
    </>
  );
}

function StageOutput({ stage, run }: { stage: StageName; run: StageRun | undefined }) {
  if (!run || run.events.length === 0) {
    return (
      <>
        <div className="text-[10px] uppercase text-zinc-600 mb-1">output · {stage}</div>
        <div className="flex-1 rounded border border-white/5 bg-surface-0 p-3 font-mono text-[10px] text-zinc-600">
          No events recorded for this stage on this chunk.
        </div>
      </>
    );
  }
  return (
    <>
      <div className="text-[10px] uppercase text-zinc-600 mb-1">
        output · {stage} · {run.invocations} invocation{run.invocations === 1 ? "" : "s"}
        {run.durationS > 0 && ` · ${(run.durationS * 1000).toFixed(0)}ms`}
      </div>
      <div className="flex-1 rounded border border-white/5 bg-surface-0 p-2 overflow-y-auto space-y-2">
        {run.events.map((e, i) => (
          <StageEventCard key={`${e.ts}-${i}`} idx={i} event={e} stage={stage} />
        ))}
      </div>
    </>
  );
}

function StageEventCard({ idx, event }: { idx: number; event: RunEvent; stage: StageName }) {
  const state = (event.state ?? {}) as Record<string, unknown>;
  const details = (event.details ?? {}) as Record<string, unknown>;
  const candidates = (state.candidate_sample as Array<Record<string, unknown>> | undefined) ?? [];
  const errors = (state.errors as Array<Record<string, unknown>> | undefined) ?? [];
  const detailErrors = (details.errors as Array<Record<string, unknown>> | undefined) ?? [];
  const allErrors = [...errors, ...detailErrors];
  // Prefer details.candidate_count — it's the real total. state.candidate_count
  // tracks the truncated sample size and is sometimes 0 for nodes that
  // don't surface a sample.
  const candidateCount =
    (details.candidate_count as number | undefined) ??
    (state.candidate_count as number | undefined) ??
    candidates.length;
  const validCount = state.valid_count as number | undefined;
  const invalidCount = state.invalid_count as number | undefined;
  const repairedCount = state.repaired_count as number | undefined;
  const retryCount = state.retry_count as number | undefined;
  const durationS = (details.duration_s as number) ?? 0;

  // Heuristic: propositions have predicate/object; mentions don't.
  const looksLikeProp = candidates[0]?.predicate != null;

  return (
    <div className="rounded border border-white/5 bg-white/[0.02] p-2 space-y-1.5">
      <div className="flex items-center gap-2 font-mono text-[10px]">
        <span className="text-zinc-500">#{idx + 1}</span>
        <span className={`px-1.5 py-0.5 rounded ${statusPill(event.status)}`}>{event.status}</span>
        {retryCount != null && retryCount > 0 && (
          <span className="text-amber-400">↻ retry {retryCount}</span>
        )}
        {repairedCount != null && (
          <span className="text-emerald-300">repaired {repairedCount}</span>
        )}
        <span className="ml-auto text-zinc-600">
          {durationS > 0 ? `${(durationS * 1000).toFixed(0)}ms` : ""}
        </span>
        <span className="text-zinc-600 text-[9.5px]">
          {new Date(event.ts).toLocaleTimeString()}
        </span>
      </div>

      <div className="flex items-center gap-3 font-mono text-[10px] text-zinc-500">
        {candidateCount != null && (
          <span>
            candidates <span className="text-zinc-300">{candidateCount}</span>
          </span>
        )}
        {validCount != null && (
          <span>
            valid <span className="text-emerald-300">{validCount}</span>
          </span>
        )}
        {invalidCount != null && invalidCount > 0 && (
          <span>
            invalid <span className="text-red-300">{invalidCount}</span>
          </span>
        )}
      </div>

      {candidates.length > 0 && (
        <div>
          <div className="text-[9.5px] uppercase text-zinc-600 mb-0.5">candidate sample</div>
          {looksLikeProp ? (
            <div className="space-y-0.5">
              {(candidates as unknown as PropositionLite[]).slice(0, 6).map((p, i) => (
                <div key={i} className="text-[10px] font-mono text-zinc-400">
                  <span className="text-cyan-300">{p.subject}</span>
                  <span className="text-zinc-600"> — </span>
                  <span className="text-zinc-200">{p.predicate}</span>
                  <span className="text-zinc-600"> → </span>
                  <span className="text-cyan-300">{p.object}</span>
                </div>
              ))}
              {candidates.length > 6 && (
                <span className="text-[9.5px] text-zinc-600">+{candidates.length - 6} more</span>
              )}
            </div>
          ) : (
            <div className="flex flex-wrap gap-1">
              {(candidates as unknown as MentionLite[]).slice(0, 12).map((m, i) => (
                <span
                  key={i}
                  className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-blue-500/15 text-blue-200 border border-blue-500/30"
                  title={`${m.type ?? m.mention_type ?? "?"} · [${m.span_start ?? "?"}, ${m.span_end ?? "?"})`}
                >
                  {m.text}
                </span>
              ))}
              {candidates.length > 12 && (
                <span className="px-1 text-[9.5px] text-zinc-600 self-center">
                  +{candidates.length - 12} more
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {allErrors.length > 0 && (
        <div>
          <div className="text-[9.5px] uppercase text-zinc-600 mb-0.5">
            errors ({allErrors.length})
          </div>
          <div className="space-y-0.5">
            {allErrors.slice(0, 8).map((er, i) => (
              <div key={i} className="text-[10px] font-mono text-zinc-400 break-words">
                <span className="text-red-300">{(er.code as string) ?? "err"}</span>
                {er.path != null && (
                  <>
                    <span className="text-zinc-600"> · </span>
                    <span className="text-zinc-500">{String(er.path)}</span>
                  </>
                )}
                {er.message != null && (
                  <>
                    <span className="text-zinc-600"> · </span>
                    <span>{String(er.message)}</span>
                  </>
                )}
              </div>
            ))}
            {allErrors.length > 8 && (
              <div className="text-[9.5px] text-zinc-600">+{allErrors.length - 8} more</div>
            )}
          </div>
        </div>
      )}

      {/* Raw fold-out for the corner cases the UI doesn't surface yet. */}
      <details>
        <summary className="text-[9.5px] uppercase text-zinc-600 cursor-pointer">raw</summary>
        <pre className="mt-1 text-[9.5px] font-mono text-zinc-500 overflow-x-auto bg-black/20 rounded p-1.5">
          {JSON.stringify({ state, details }, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function statusPill(status: string): string {
  if (status === "completed" || status === "valid") return "bg-emerald-500/20 text-emerald-300";
  if (status === "ambiguous") return "bg-amber-500/20 text-amber-300";
  if (status === "invalid" || status === "error" || status === "failed")
    return "bg-red-500/20 text-red-300";
  if (status === "started") return "bg-blue-500/20 text-blue-300";
  return "bg-zinc-500/20 text-zinc-300";
}
