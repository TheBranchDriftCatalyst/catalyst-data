/**
 * DocumentSourcePanel — full document text with chunk-boundary overlays.
 *
 * Lives in the top-right of StateInspectorV2. Shows the entire raw text
 * the pipeline operated on, with subtle separators between chunks (so the
 * chunker's strategy is visible in context) and a highlighted span for
 * whichever chunk corresponds to the currently-selected graph node.
 *
 * Data source:
 *   - The full document text comes from any ``chunk_loaded`` event whose
 *     chunk_id is the per-encoder doc-scoped form (``{doc_id}:_ner_<enc>``);
 *     these carry the entire raw_text the encoder saw, capped at 4 KiB by
 *     event_tail.emit_chunk_text.  We pick the longest such text as the
 *     source-of-truth.
 *   - Chunk boundaries are drawn from the pre-NER chunk_loaded events
 *     (``{doc_id}:chunk-N`` style) when present.  Each carries chunk_index
 *     + char_count, which we accumulate to compute char ranges.
 *   - SPO windows (``{doc_id}:win-<hash>``) come from the EvidenceWindow
 *     records persisted in the audit log; if their doc_char_start /
 *     doc_char_end are present we render those as highlights too.
 *
 * Selected highlight:
 *   - When the user clicks an spo_window node we highlight its char range.
 *   - When the user clicks a per-encoder NER node we have nothing more
 *     specific to highlight than the whole doc, so we show the encoder's
 *     mention spans (from the chunk_extracted event details) as inline
 *     coloured underlines.
 */

import { useMemo } from "react";

import type { RunEvent } from "@/types/benchmark";
import type { SelectedGraphNode } from "@/components/state/PipelineGraph";

interface Props {
  events: RunEvent[];
  docId: string;
  selectedNode: SelectedGraphNode | null;
}

interface ChunkSpan {
  chunkId: string;
  start: number;
  end: number;
  kind: "input" | "window";
  label: string;
}

interface MentionSpan {
  start: number;
  end: number;
  type: string;
  text: string;
}

interface MentionLite {
  text?: string;
  mention_type?: string;
  type?: string;
  span_start?: number | null;
  span_end?: number | null;
}

function _findFullDocText(events: RunEvent[], docId: string): string | null {
  // Prefer the longest chunk_loaded text whose chunk_id is {docId}:_ner_<enc>
  // — those carry the entire raw_text the encoder saw.
  let best: { text: string; len: number } | null = null;
  for (const e of events) {
    if (e.node_name !== "chunk_loaded" || !e.chunk_id) continue;
    if (!e.chunk_id.startsWith(`${docId}:`)) continue;
    if (!e.chunk_id.includes(":_ner_")) continue;
    const text = ((e.details ?? {}) as { text?: string }).text ?? "";
    if (!text) continue;
    if (!best || text.length > best.len) best = { text, len: text.length };
  }
  // Fallback: concatenate pre-NER chunk_loaded texts in chunk_index order.
  if (!best) {
    const preChunks: { idx: number; text: string }[] = [];
    for (const e of events) {
      if (e.node_name !== "chunk_loaded" || !e.chunk_id) continue;
      const cid = e.chunk_id;
      if (
        cid.includes(":_ner_") ||
        cid.endsWith(":_consensus") ||
        cid.includes(":win-") ||
        cid.endsWith(":_doc_ensemble")
      )
        continue;
      if (!cid.startsWith(`${docId}:`)) continue;
      const d = (e.details ?? {}) as { text?: string; chunk_index?: number | null };
      if (!d.text) continue;
      preChunks.push({ idx: d.chunk_index ?? preChunks.length, text: d.text });
    }
    if (preChunks.length > 0) {
      preChunks.sort((a, b) => a.idx - b.idx);
      best = {
        text: preChunks.map((c) => c.text).join("\n\n"),
        len: 0,
      };
    }
  }
  return best ? best.text : null;
}

function _collectChunkSpans(events: RunEvent[], docId: string, fullText: string): ChunkSpan[] {
  const spans: ChunkSpan[] = [];
  // Pre-NER input chunks: build ranges by accumulating chunk lengths in
  // chunk_index order. This is approximate (no overlap-aware accounting)
  // but sufficient for a visual boundary overlay.
  const preChunks: { idx: number; chunkId: string; text: string }[] = [];
  for (const e of events) {
    if (e.node_name !== "chunk_loaded" || !e.chunk_id) continue;
    const cid = e.chunk_id;
    if (
      cid.includes(":_ner_") ||
      cid.endsWith(":_consensus") ||
      cid.includes(":win-") ||
      cid.endsWith(":_doc_ensemble")
    )
      continue;
    if (!cid.startsWith(`${docId}:`)) continue;
    const d = (e.details ?? {}) as { text?: string; chunk_index?: number | null };
    if (!d.text) continue;
    preChunks.push({
      idx: d.chunk_index ?? preChunks.length,
      chunkId: cid,
      text: d.text,
    });
  }
  preChunks.sort((a, b) => a.idx - b.idx);
  let cursor = 0;
  for (const c of preChunks) {
    // Try a localized search forward from the cursor — handles minor
    // whitespace drift between concatenated chunks and the fullText.
    const probe = c.text.slice(0, Math.min(60, c.text.length));
    const found = fullText.indexOf(probe, cursor);
    const start = found >= 0 ? found : cursor;
    const end = Math.min(fullText.length, start + c.text.length);
    spans.push({
      chunkId: c.chunkId,
      start,
      end,
      kind: "input",
      label: `chunk #${c.idx}`,
    });
    cursor = end;
  }
  // SPO window spans: pulled from chunk_loaded.details for :win- chunks
  // that may carry doc_char_start/end. Fall back to text-search.
  for (const e of events) {
    if (e.node_name !== "chunk_loaded" || !e.chunk_id) continue;
    const cid = e.chunk_id;
    if (!cid.includes(":win-") || !cid.startsWith(`${docId}:`)) continue;
    const d = (e.details ?? {}) as {
      text?: string;
      doc_char_start?: number | null;
      doc_char_end?: number | null;
    };
    let start = d.doc_char_start ?? -1;
    let end = d.doc_char_end ?? -1;
    if ((start < 0 || end < 0) && d.text) {
      const probe = d.text.slice(0, Math.min(60, d.text.length));
      const idx = fullText.indexOf(probe);
      if (idx >= 0) {
        start = idx;
        end = idx + d.text.length;
      }
    }
    if (start >= 0 && end > start) {
      spans.push({
        chunkId: cid,
        start,
        end: Math.min(end, fullText.length),
        kind: "window",
        label: cid.split(":win-")[1]?.slice(0, 6) ?? "win",
      });
    }
  }
  return spans;
}

function _collectMentionSpans(events: RunEvent[], docId: string, encoder: string): MentionSpan[] {
  const ext = events.find(
    (e) => e.node_name === "chunk_extracted" && e.chunk_id === `${docId}:_ner_${encoder}`,
  );
  if (!ext) return [];
  const mentions = ((ext.details ?? {}) as { mentions?: MentionLite[] }).mentions ?? [];
  const out: MentionSpan[] = [];
  for (const m of mentions) {
    if (m.span_start == null || m.span_end == null) continue;
    out.push({
      start: m.span_start,
      end: m.span_end,
      type: m.mention_type ?? m.type ?? "",
      text: m.text ?? "",
    });
  }
  return out;
}

function _selectedChunkId(node: SelectedGraphNode | null): string | null {
  if (!node) return null;
  if (node.role === "spo_window" && node.ref) return node.ref;
  if (node.role === "consensus") return null;
  return null;
}

const KIND_HIGHLIGHT: Record<ChunkSpan["kind"], string> = {
  input: "bg-zinc-700/20 border-l border-zinc-600",
  window: "bg-amber-500/15 border-l border-amber-500/60",
};

export function DocumentSourcePanel({ events, docId, selectedNode }: Props) {
  const fullText = useMemo(() => _findFullDocText(events, docId), [events, docId]);
  const chunkSpans = useMemo(
    () => (fullText ? _collectChunkSpans(events, docId, fullText) : []),
    [events, docId, fullText],
  );
  const mentionSpans = useMemo(() => {
    if (!selectedNode || selectedNode.role !== "ner_encoder" || !selectedNode.ref) return [];
    return _collectMentionSpans(events, docId, selectedNode.ref);
  }, [events, docId, selectedNode]);

  const selectedChunkId = _selectedChunkId(selectedNode);

  const stats = useMemo(() => {
    if (!fullText) return null;
    const inputCount = chunkSpans.filter((s) => s.kind === "input").length;
    const windowCount = chunkSpans.filter((s) => s.kind === "window").length;
    return { chars: fullText.length, inputCount, windowCount };
  }, [fullText, chunkSpans]);

  if (!fullText) {
    return (
      <div className="p-4 font-mono text-[10px] text-zinc-600">
        No document text observed yet for {docId}.
        <br />
        Start a benchmark run, or pick a different doc on the left.
      </div>
    );
  }

  // Build segment array for rendering: walk fullText and slice by the union
  // of chunk-boundary positions. Each segment carries the chunk it belongs
  // to (for tooltip + highlight) and any mention spans it overlaps.
  const segments = _buildSegments(fullText, chunkSpans, mentionSpans, selectedChunkId);

  return (
    <div className="flex flex-col h-full">
      <div className="sticky top-0 z-10 bg-surface-1/95 backdrop-blur border-b border-white/5 px-3 py-2 font-mono text-[10px] text-zinc-400 flex items-center gap-3">
        <span className="text-zinc-300">document source</span>
        {stats && (
          <>
            <span className="text-zinc-600">{stats.chars.toLocaleString()} chars</span>
            <span className="text-zinc-600">·</span>
            <span className="text-zinc-600">{stats.inputCount} input chunks</span>
            {stats.windowCount > 0 && (
              <>
                <span className="text-zinc-600">·</span>
                <span className="text-amber-400">{stats.windowCount} SPO windows</span>
              </>
            )}
          </>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-zinc-300">
        {segments.map((seg, i) => (
          <span
            key={i}
            className={[
              seg.chunkKind ? KIND_HIGHLIGHT[seg.chunkKind] : "",
              seg.isSelected ? "bg-cyan-500/30 ring-1 ring-cyan-400" : "",
              seg.mentionType ? "underline decoration-violet-400 decoration-2" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            title={
              seg.mentionType
                ? `${seg.mentionType}: ${seg.mentionText}`
                : seg.chunkLabel
                  ? `${seg.chunkLabel} (${seg.chunkId})`
                  : undefined
            }
          >
            {seg.text}
          </span>
        ))}
      </div>
    </div>
  );
}

interface RenderSegment {
  text: string;
  chunkKind: ChunkSpan["kind"] | null;
  chunkId: string | null;
  chunkLabel: string | null;
  isSelected: boolean;
  mentionType: string | null;
  mentionText: string | null;
}

function _buildSegments(
  fullText: string,
  chunkSpans: ChunkSpan[],
  mentionSpans: MentionSpan[],
  selectedChunkId: string | null,
): RenderSegment[] {
  // Collect all break points (start/end of every span). Walk in order,
  // emit a segment between each pair of breaks with the active styling.
  const breaks = new Set<number>([0, fullText.length]);
  for (const s of chunkSpans) {
    breaks.add(s.start);
    breaks.add(s.end);
  }
  for (const m of mentionSpans) {
    breaks.add(m.start);
    breaks.add(m.end);
  }
  const sorted = [...breaks].filter((p) => p >= 0 && p <= fullText.length).sort((a, b) => a - b);
  const segs: RenderSegment[] = [];
  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i]!;
    const b = sorted[i + 1]!;
    if (a >= b) continue;
    const slice = fullText.slice(a, b);
    if (!slice) continue;
    // Find the most-specific chunk this segment falls inside (windows
    // win over input chunks since they're the more specific selection).
    let chunkKind: ChunkSpan["kind"] | null = null;
    let chunkId: string | null = null;
    let chunkLabel: string | null = null;
    let isSelected = false;
    for (const s of chunkSpans) {
      if (a >= s.start && b <= s.end) {
        if (s.kind === "window" || chunkKind === null) {
          chunkKind = s.kind;
          chunkId = s.chunkId;
          chunkLabel = s.label;
          if (selectedChunkId && s.chunkId === selectedChunkId) isSelected = true;
        }
      }
    }
    let mentionType: string | null = null;
    let mentionText: string | null = null;
    for (const m of mentionSpans) {
      if (a >= m.start && b <= m.end) {
        mentionType = m.type;
        mentionText = m.text;
        break;
      }
    }
    segs.push({
      text: slice,
      chunkKind,
      chunkId,
      chunkLabel,
      isSelected,
      mentionType,
      mentionText,
    });
  }
  return segs;
}
