/**
 * Right column of the StateInspector — full provenance + chunking
 * strategy + source-text view for the currently-selected chunk.
 *
 * The point of this pane is to let an operator diagnose chunker
 * problems: is the size right for the model, is the speaker carry
 * working, is the char offset where it should be, is the text being
 * truncated by emit_chunk_text. Everything the chunker put into
 * `metadata` is surfaced here, plus the temporal/speaker fields
 * promoted off the audio chunker.
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
    if (sp.start < cursor) continue;
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

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 text-[11px] font-mono">
      <span className="text-zinc-600 w-24 flex-shrink-0">{label}</span>
      <span className="text-zinc-300 break-all">
        {value ?? <span className="text-zinc-600">—</span>}
      </span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase text-zinc-600 mb-1 tracking-wide">{title}</div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

export function ChunkTextPanel({ chunkText, extracted, hoveredErrorIndex }: Props) {
  const text = (chunkText?.text as string | undefined) ?? "";
  const truncated = chunkText?.truncated as boolean | undefined;
  const charCount = chunkText?.char_count as number | undefined;
  const domain = chunkText?.domain as string | undefined;
  const speaker = chunkText?.speaker_label as string | undefined;
  const tStart = chunkText?.temporal_start_ms as number | undefined;
  const tEnd = chunkText?.temporal_end_ms as number | undefined;
  const chunkIndex = chunkText?.chunk_index as number | undefined;
  const totalChunks = chunkText?.total_chunks as number | undefined;
  const cmeta = (chunkText?.chunk_metadata as Record<string, unknown> | undefined) ?? {};

  const mentions = (extracted?.mentions as MentionLite[] | undefined) ?? [];
  const propositions = (extracted?.propositions as PropositionLite[] | undefined) ?? [];

  const spans = useMemo(
    () => buildSpans(mentions, hoveredErrorIndex),
    [mentions, hoveredErrorIndex],
  );

  if (!chunkText) {
    return (
      <div className="p-4 font-mono text-xs text-zinc-600">
        Pick a chunk in the timeline to see provenance + chunking strategy.
      </div>
    );
  }

  // Promoted chunk metadata fields the chunker writes (recursive,
  // speaker_turn, etc).
  const strategy = cmeta.strategy as string | undefined;
  const chunkSize = cmeta.chunk_size as number | undefined;
  const chunkOverlap = cmeta.chunk_overlap as number | undefined;
  const charOffset = cmeta.chunk_char_offset as number | undefined;
  const startS = cmeta.start_s as number | undefined;
  const endS = cmeta.end_s as number | undefined;
  // Anything not already surfaced — show in a raw fold-out at the bottom.
  const KNOWN_KEYS = new Set([
    "strategy",
    "chunk_size",
    "chunk_overlap",
    "chunk_char_offset",
    "start_s",
    "end_s",
    "speaker",
    "domain",
  ]);
  const extras = Object.entries(cmeta).filter(([k]) => !KNOWN_KEYS.has(k));

  return (
    <div className="p-3 space-y-4">
      <Section title="provenance">
        <MetaRow label="domain" value={domain} />
        <MetaRow
          label="chunk"
          value={
            chunkIndex != null
              ? `#${chunkIndex}${totalChunks != null ? ` / ${totalChunks}` : ""}`
              : "—"
          }
        />
        <MetaRow
          label="char range"
          value={
            charOffset != null && charCount != null
              ? `${charOffset}–${charOffset + charCount}`
              : charCount != null
                ? `${charCount} chars`
                : "—"
          }
        />
        {(speaker || tStart != null || startS != null) && (
          <>
            <MetaRow label="speaker" value={speaker} />
            <MetaRow
              label="time"
              value={
                tStart != null && tEnd != null
                  ? `${(tStart / 1000).toFixed(1)}s – ${(tEnd / 1000).toFixed(1)}s`
                  : startS != null && endS != null
                    ? `${startS.toFixed(1)}s – ${endS.toFixed(1)}s`
                    : "—"
              }
            />
          </>
        )}
      </Section>

      <Section title="chunking strategy">
        <MetaRow
          label="strategy"
          value={strategy ? <span className="text-emerald-300">{strategy}</span> : "—"}
        />
        <MetaRow label="chunk_size" value={chunkSize} />
        <MetaRow label="chunk_overlap" value={chunkOverlap} />
        {extras.length > 0 && (
          <details className="mt-1">
            <summary className="text-[10px] uppercase text-zinc-600 cursor-pointer">
              extra metadata ({extras.length})
            </summary>
            <pre className="mt-1 text-[10px] font-mono text-zinc-400 overflow-x-auto bg-surface-0 border border-white/5 rounded p-2">
              {JSON.stringify(Object.fromEntries(extras), null, 2)}
            </pre>
          </details>
        )}
      </Section>

      <Section title={`source text · ${mentions.length} spans`}>
        <div className="text-xs font-mono leading-relaxed whitespace-pre-wrap break-words bg-surface-0 border border-white/5 rounded p-2 max-h-96 overflow-y-auto">
          {renderTextWithSpans(text, spans)}
        </div>
        {truncated && (
          <div className="text-[10px] text-amber-400 font-mono mt-1">
            text truncated to {text.length} of {charCount} chars (event_tail max_chars cap)
          </div>
        )}
      </Section>

      {propositions.length > 0 && (
        <Section title={`propositions · ${propositions.length}`}>
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
        </Section>
      )}
    </div>
  );
}
