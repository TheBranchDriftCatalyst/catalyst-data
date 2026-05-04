/**
 * DocumentSourcePanel — full document text with chunk-boundary overlays.
 *
 * Lives in the top-right of StateInspector. Shows the entire raw text the
 * pipeline operated on, with subtle separators between chunks (so the
 * chunker's strategy is visible in context) and a highlighted span for
 * whichever chunk corresponds to the currently-selected graph node.
 *
 * Data source: ``GET /viewer/api/docs/<doc_id>/text`` (defined in
 * packages/media-ingest/.../routes/docs.py). Returns the full doc text +
 * per-chunk char ranges resolved from the silver layer:
 *
 *   1. *_chunks asset rows for the doc — gives authoritative chunk
 *      boundaries (one entry per chunk, with chunk_id + start/end).
 *   2. Doc-type-aware fallback (media_segment_merge for video docs,
 *      bill_documents / leak_documents for legal docs) — single chunk
 *      spanning the full text.
 *
 * Selected highlight:
 *   - When the user clicks an spo_window node we highlight its char range
 *     inside the doc (looked up from chunk_loaded events).
 *   - When the user clicks a per-encoder NER node we render the encoder's
 *     mention spans (from chunk_extracted) as inline coloured underlines.
 */

import { useEffect, useMemo, useState } from "react";

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
  index: number | null;
  preview: string;
  metadata: Record<string, unknown>;
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

interface DocPayload {
  doc_id: string;
  source: string;
  text: string;
  char_count: number;
  chunks: Array<{
    chunk_id: string | null;
    index: number | null;
    total_chunks: number | null;
    start: number;
    end: number;
    char_count: number;
    text_preview: string;
    metadata: Record<string, unknown>;
  }>;
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
  return null;
}

export function DocumentSourcePanel({ events, docId, selectedNode }: Props) {
  const [payload, setPayload] = useState<DocPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPayload(null);
    setError(null);
    setLoading(true);
    fetch(`/viewer/api/docs/${encodeURIComponent(docId)}/text`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
        return (await r.json()) as DocPayload;
      })
      .then((p) => {
        if (!cancelled) setPayload(p);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [docId]);

  const chunkSpans = useMemo<ChunkSpan[]>(() => {
    if (!payload) return [];
    return payload.chunks.map((c) => ({
      chunkId: c.chunk_id ?? `${payload.doc_id}:chunk-${c.index ?? 0}`,
      start: c.start,
      end: c.end,
      index: c.index,
      preview: c.text_preview,
      metadata: c.metadata,
    }));
  }, [payload]);

  const mentionSpans = useMemo(() => {
    if (!selectedNode || selectedNode.role !== "ner_encoder" || !selectedNode.ref) return [];
    return _collectMentionSpans(events, docId, selectedNode.ref);
  }, [events, docId, selectedNode]);

  const selectedChunkId = _selectedChunkId(selectedNode);

  if (loading && !payload) {
    return <div className="p-4 font-mono text-[10px] text-zinc-500">Loading {docId}…</div>;
  }
  if (error) {
    return (
      <div className="p-4 font-mono text-[10px] text-red-300">
        Failed to load {docId}: {error}
      </div>
    );
  }
  if (!payload) {
    return (
      <div className="p-4 font-mono text-[10px] text-zinc-600">
        No document text observed yet for {docId}.
      </div>
    );
  }

  // Build segment array for rendering: walk fullText and slice by the union
  // of chunk-boundary positions. Each segment carries the chunk it belongs
  // to (for tooltip + highlight) and any mention spans it overlaps.
  const segments = _buildSegments(payload.text, chunkSpans, mentionSpans, selectedChunkId);

  return (
    <div className="flex flex-col h-full">
      <div className="sticky top-0 z-10 bg-surface-1/95 backdrop-blur border-b border-white/5 px-3 py-2 font-mono text-[10px] text-zinc-400 flex items-center gap-3">
        <span className="text-zinc-300">document source</span>
        <span className="text-zinc-600">{payload.char_count.toLocaleString()} chars</span>
        <span className="text-zinc-600">·</span>
        <span className="text-zinc-600">{chunkSpans.length} chunks</span>
        <span className="text-zinc-600">·</span>
        <span className="text-zinc-500 text-[9px]">source: {payload.source}</span>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-zinc-300">
        {segments.map((seg, i) => (
          <span
            key={i}
            className={[
              seg.chunkId ? "border-l border-zinc-700/60" : "",
              seg.isSelected ? "bg-cyan-500/30 ring-1 ring-cyan-400" : "",
              seg.mentionType ? "underline decoration-violet-400 decoration-2" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            title={
              seg.mentionType
                ? `${seg.mentionType}: ${seg.mentionText}`
                : seg.chunkLabel
                  ? `${seg.chunkLabel}\n${seg.chunkPreview}`
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
  chunkId: string | null;
  chunkLabel: string | null;
  chunkPreview: string | null;
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
    let chunkId: string | null = null;
    let chunkLabel: string | null = null;
    let chunkPreview: string | null = null;
    let isSelected = false;
    for (const s of chunkSpans) {
      if (a >= s.start && b <= s.end) {
        chunkId = s.chunkId;
        chunkLabel = s.index != null ? `chunk #${s.index}` : s.chunkId;
        chunkPreview = s.preview;
        if (selectedChunkId && s.chunkId === selectedChunkId) isSelected = true;
        break;
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
      chunkId,
      chunkLabel,
      chunkPreview,
      isSelected,
      mentionType,
      mentionText,
    });
  }
  return segs;
}
