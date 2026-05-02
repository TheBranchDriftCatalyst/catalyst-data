/**
 * Right column of the StateInspector — shows the chunk's source text
 * (from the one-shot `chunk_loaded` event) with span overlays for the
 * final accepted mentions, plus the terminal NER + SPO output for
 * this (model × chunk).
 *
 * Hovering an error in the EventStream's center column highlights the
 * offending candidate's span here so span misalignments are visible
 * at a glance.
 */

import { useMemo } from "react";

import type { MentionLite, PropositionLite, RunEvent } from "@/types/benchmark";

interface Props {
  chunkText: Record<string, unknown> | null;
  extracted: Record<string, unknown> | null;
  events: RunEvent[];
  hoveredErrorIndex: number | null;
}

interface Span {
  start: number;
  end: number;
  label: string;
  highlight: boolean;
}

function buildSpans(mentions: MentionLite[], highlightIdx: number | null): Span[] {
  return mentions
    .map((m, i) => {
      const start = (m.span_start ?? m.span?.[0]) as number | null | undefined;
      const end = (m.span_end ?? m.span?.[1]) as number | null | undefined;
      if (start == null || end == null) return null;
      return {
        start,
        end,
        label: m.type ?? m.mention_type ?? "?",
        highlight: highlightIdx === i,
      } as Span;
    })
    .filter((s): s is Span => s !== null)
    .sort((a, b) => a.start - b.start);
}

function renderTextWithSpans(text: string, spans: Span[]) {
  if (spans.length === 0) {
    return <span className="text-zinc-300">{text}</span>;
  }
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (let i = 0; i < spans.length; i++) {
    const sp = spans[i]!;
    if (sp.start < cursor) continue; // overlap; skip
    if (sp.start > cursor) {
      out.push(
        <span key={`t-${i}`} className="text-zinc-400">
          {text.slice(cursor, sp.start)}
        </span>,
      );
    }
    out.push(
      <span
        key={`s-${i}`}
        className={
          sp.highlight
            ? "bg-amber-500/40 text-amber-100 rounded-sm px-0.5"
            : "bg-blue-500/20 text-blue-100 rounded-sm px-0.5"
        }
        title={`${sp.label} · [${sp.start}, ${sp.end})`}
      >
        {text.slice(sp.start, sp.end)}
      </span>,
    );
    cursor = sp.end;
  }
  if (cursor < text.length) {
    out.push(
      <span key="tail" className="text-zinc-400">
        {text.slice(cursor)}
      </span>,
    );
  }
  return out;
}

export function ChunkTextPanel({ chunkText, extracted, hoveredErrorIndex }: Props) {
  const text = (chunkText?.text as string | undefined) ?? "";
  const truncated = chunkText?.truncated as boolean | undefined;
  const charCount = chunkText?.char_count as number | undefined;
  const domain = chunkText?.domain as string | undefined;
  const speaker = chunkText?.speaker_label as string | undefined;
  const tStart = chunkText?.temporal_start_ms as number | undefined;
  const tEnd = chunkText?.temporal_end_ms as number | undefined;

  const mentions = (extracted?.mentions as MentionLite[] | undefined) ?? [];
  const propositions = (extracted?.propositions as PropositionLite[] | undefined) ?? [];

  const spans = useMemo(
    () => buildSpans(mentions, hoveredErrorIndex),
    [mentions, hoveredErrorIndex],
  );

  if (!chunkText) {
    return (
      <div className="p-4 font-mono text-xs text-zinc-600">Waiting for `chunk_loaded` event…</div>
    );
  }

  return (
    <div className="p-3 space-y-3">
      <div>
        <div className="text-[10px] uppercase text-zinc-600 mb-1">chunk</div>
        <div className="text-[11px] font-mono text-zinc-400 space-y-0.5">
          {domain && (
            <div>
              domain: <span className="text-zinc-300">{domain}</span>
            </div>
          )}
          {speaker && (
            <div>
              speaker: <span className="text-zinc-300">{speaker}</span>
            </div>
          )}
          {tStart != null && tEnd != null && (
            <div>
              time:{" "}
              <span className="text-zinc-300">
                {(tStart / 1000).toFixed(1)}s – {(tEnd / 1000).toFixed(1)}s
              </span>
            </div>
          )}
          {charCount != null && (
            <div>
              chars: <span className="text-zinc-300">{charCount}</span>
              {truncated && <span className="text-amber-400"> (truncated to {text.length})</span>}
            </div>
          )}
        </div>
      </div>

      <div>
        <div className="text-[10px] uppercase text-zinc-600 mb-1">
          source text · {mentions.length} spans
        </div>
        <div className="text-xs font-mono leading-relaxed whitespace-pre-wrap break-words bg-surface-0 border border-white/5 rounded p-2">
          {renderTextWithSpans(text, spans)}
        </div>
      </div>

      {propositions.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-zinc-600 mb-1">
            propositions · {propositions.length}
          </div>
          <div className="space-y-1">
            {propositions.map((p, i) => (
              <div
                key={i}
                className="text-[11px] font-mono text-zinc-300 bg-surface-0 border border-white/5 rounded p-1.5"
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
    </div>
  );
}
