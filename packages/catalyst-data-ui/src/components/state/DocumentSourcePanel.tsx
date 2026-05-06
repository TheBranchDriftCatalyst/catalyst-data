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

import { useEffect, useMemo, useRef, useState } from "react";

import type { RunEvent } from "@/types/benchmark";
import type { SelectedGraphNode } from "@/components/state/PipelineGraph";
import { useActiveGroundTruth } from "@/hooks/useRunReport";

import { DocCoverageGutter } from "./DocCoverageGutter";
import { DeepLinkButton } from "./DeepLinkButton";
import { PathologyRollup } from "./PathologyRollup";

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
  /** Which pipeline stage the mention came from — drives the
   *  ``data-mention-source`` attribute used by the e2e suite. */
  source?: "encoder" | "consensus";
}

interface PackWindowOverlay {
  start: number;
  end: number;
  windowId: string;
  status: "kept" | "pruned";
  mentionCount: number;
  reason?: string;
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
      source: "encoder",
    });
  }
  return out;
}

function _collectConsensusSpans(events: RunEvent[], docId: string): MentionSpan[] {
  // Consensus emits chunk_extracted with chunk_id "<doc>:_consensus" carrying
  // the accepted-mention list (each item has span_start/span_end/text plus
  // canonical_type or mention_type). Surface these the same way encoder
  // mentions are surfaced — inline violet underline on the doc text.
  const ext = events.find(
    (e) => e.node_name === "chunk_extracted" && e.chunk_id === `${docId}:_consensus`,
  );
  if (!ext) return [];
  const details = (ext.details ?? {}) as {
    mentions?: (MentionLite & { canonical_type?: string })[];
    accepted?: (MentionLite & { canonical_type?: string })[];
  };
  const list = details.mentions ?? details.accepted ?? [];
  const out: MentionSpan[] = [];
  for (const m of list) {
    if (m.span_start == null || m.span_end == null) continue;
    out.push({
      start: m.span_start,
      end: m.span_end,
      type: (m as { canonical_type?: string }).canonical_type ?? m.mention_type ?? m.type ?? "",
      text: m.text ?? "",
      source: "consensus",
    });
  }
  return out;
}

function _selectedChunkId(node: SelectedGraphNode | null): string | null {
  if (!node) return null;
  if (node.role === "spo_window" && node.ref) return node.ref;
  return null;
}

interface SelectedWindow {
  windowId: string;
  start: number;
  end: number;
  mentionCount: number;
  status: "kept";
}

/** Resolve a selected ``spo_window`` node → its char range on the doc.
 *
 *  ``spo_window`` chunk_ids are ``<doc_id>:win-<hash>``; the authoritative
 *  start/end offsets live on the ``pack_evidence`` completion event's
 *  ``kept_windows[]`` array (see ``nodes/pack.py``). We don't try to
 *  resolve pruned windows here — they don't carry doc_char offsets, and
 *  the inspector dispatches pruned clicks to a separate detail panel.
 */
function _selectedWindowRange(
  events: RunEvent[],
  docId: string,
  node: SelectedGraphNode | null,
): SelectedWindow | null {
  if (!node || node.role !== "spo_window" || !node.ref) return null;
  const winId = node.ref.split(":win-")[1];
  if (!winId) return null;
  const targetWindowId = `win-${winId}`;

  const docPrefix = `${docId}:`;
  const packEvent = events.find(
    (e) =>
      e.node_name === "pack_evidence" &&
      e.status === "completed" &&
      (e.doc_id === docId || e.chunk_id?.startsWith(docPrefix)),
  );
  if (!packEvent) return null;
  const kept = ((packEvent.details ?? {}) as { kept_windows?: Array<Record<string, unknown>> })
    .kept_windows;
  if (!Array.isArray(kept)) return null;
  for (const w of kept) {
    if ((w.window_id as string) !== targetWindowId) continue;
    const start = w.doc_char_start as number | null | undefined;
    const end = w.doc_char_end as number | null | undefined;
    if (typeof start !== "number" || typeof end !== "number") return null;
    return {
      windowId: targetWindowId,
      start,
      end,
      mentionCount: (w.mention_count as number) ?? 0,
      status: "kept",
    };
  }
  return null;
}

function _collectPackWindows(events: RunEvent[], docId: string): PackWindowOverlay[] {
  const out: PackWindowOverlay[] = [];
  const docPrefix = `${docId}:`;
  // Kept windows live inside the pack_evidence completion event's
  // kept_windows array (with doc_char_start/doc_char_end). Pruned ones are
  // emitted per-window as evidence_window_pruned and don't include offsets,
  // so we only show them in tooltip mode without a positional band.
  const packEvent = events.find(
    (e) =>
      e.node_name === "pack_evidence" &&
      e.status === "completed" &&
      (e.doc_id === docId || e.chunk_id?.startsWith(docPrefix)),
  );
  if (packEvent) {
    const d = (packEvent.details ?? {}) as Record<string, unknown>;
    const kept = (d.kept_windows as Array<Record<string, unknown>>) ?? [];
    for (const w of kept) {
      const start = w.doc_char_start as number | null | undefined;
      const end = w.doc_char_end as number | null | undefined;
      if (typeof start !== "number" || typeof end !== "number") continue;
      out.push({
        start,
        end,
        windowId: (w.window_id as string) ?? "",
        status: "kept",
        mentionCount: (w.mention_count as number) ?? 0,
      });
    }
  }
  return out;
}

interface ChunkStatusMap {
  prunedWindowCount: Set<string>;
  acceptedWindowCount: Set<string>;
}

function _buildChunkStatusFromEvents(events: RunEvent[], docId: string): ChunkStatusMap {
  const pruned = new Set<string>();
  const accepted = new Set<string>();
  const docPrefix = `${docId}:`;
  for (const e of events) {
    if (!e.chunk_id || !e.chunk_id.startsWith(docPrefix)) continue;
    if (e.node_name === "evidence_window_pruned") {
      const wid = ((e.details ?? {}) as { window_id?: string }).window_id;
      if (wid) pruned.add(wid);
    } else if (
      e.node_name === "chunk_extracted" &&
      e.chunk_id.includes(":win-") &&
      e.status === "completed"
    ) {
      accepted.add(e.chunk_id);
    }
  }
  return { prunedWindowCount: pruned, acceptedWindowCount: accepted };
}

type ChunkStatusLabel = "selected" | "pruned" | "accepted" | "input";

function _chunkStatusColor(
  chunkId: string,
  status: ChunkStatusMap,
  isSelected: boolean,
): { bg: string; label: ChunkStatusLabel } {
  if (isSelected) return { bg: "bg-cyan-400", label: "selected" };
  if (status.prunedWindowCount.has(chunkId)) return { bg: "bg-amber-500/70", label: "pruned" };
  if (status.acceptedWindowCount.has(chunkId))
    return { bg: "bg-emerald-500/60", label: "accepted" };
  return { bg: "bg-zinc-600/50", label: "input" };
}

function _chunkBodyStyle(label: ChunkStatusLabel): {
  bg: string;
  border: string;
  chip: string;
  chipDot: string;
} {
  switch (label) {
    case "selected":
      return {
        bg: "bg-cyan-500/10",
        border: "border-l-2 border-cyan-400",
        chip: "bg-cyan-500/25 text-cyan-200 border-cyan-400/60",
        chipDot: "bg-cyan-300",
      };
    case "accepted":
      return {
        bg: "bg-emerald-500/[0.05]",
        border: "border-l-2 border-emerald-500/55",
        chip: "bg-emerald-500/20 text-emerald-300 border-emerald-500/45",
        chipDot: "bg-emerald-400",
      };
    case "pruned":
      return {
        bg: "bg-amber-500/[0.06]",
        border: "border-l-2 border-amber-500/55",
        chip: "bg-amber-500/20 text-amber-300 border-amber-500/45",
        chipDot: "bg-amber-400",
      };
    default:
      return {
        bg: "",
        border: "border-l-2 border-zinc-700/70",
        chip: "bg-zinc-800/80 text-zinc-400 border-zinc-700",
        chipDot: "bg-zinc-500",
      };
  }
}

export function DocumentSourcePanel({ events, docId, selectedNode }: Props) {
  const [payload, setPayload] = useState<DocPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const { data: gtList, status: gtStatus } = useActiveGroundTruth();

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

  // Always-on consensus span collection — feeds the right-edge coverage
  // gutter (Gap #6). The mention-overlay path below still gates on the
  // selected node, but the gutter wants the same data regardless of what
  // the user has clicked, so we hoist this scan out of the selection
  // branch. Single events scan; the cost is amortised over the panel's
  // lifetime.
  const docConsensusSpans = useMemo(() => _collectConsensusSpans(events, docId), [events, docId]);

  const mentionSpans = useMemo(() => {
    if (!selectedNode) return [];
    if (selectedNode.role === "ner_encoder" && selectedNode.ref) {
      return _collectMentionSpans(events, docId, selectedNode.ref);
    }
    if (selectedNode.role === "consensus") {
      return docConsensusSpans;
    }
    return [];
  }, [events, docId, selectedNode, docConsensusSpans]);

  // GT spans scoped to the current doc — fed to the coverage gutter so
  // it can paint the amber recall-hole track. Mirrors Gap #1 / Gap #3:
  //   - status !== "success" → null (gutter renders cyan-only)
  //   - status === "success" with 0 doc-scoped mentions → [] (also
  //     cyan-only) — distinguishing here mostly to keep the contract
  //     explicit; the gutter handles both identically.
  const docGtSpans = useMemo(() => {
    if (gtStatus !== "success" || !gtList) return null;
    const out: { start: number; end: number }[] = [];
    for (const m of gtList) {
      if (m.doc_id !== docId) continue;
      if (m.span_start == null || m.span_end == null) continue;
      out.push({ start: m.span_start, end: m.span_end });
    }
    return out;
  }, [gtList, gtStatus, docId]);

  const packWindows = useMemo<PackWindowOverlay[]>(() => {
    if (!selectedNode || selectedNode.role !== "pack") return [];
    return _collectPackWindows(events, docId);
  }, [events, docId, selectedNode]);

  // ``spo_window`` selection → resolve its char range via pack_evidence so
  // the doc panel paints a saturated cyan overlay and scrolls to it. None
  // when the selection isn't a window (or the window's pack record is
  // missing offsets).
  const selectedWindow = useMemo(
    () => _selectedWindowRange(events, docId, selectedNode),
    [events, docId, selectedNode],
  );

  const selectedChunkId = _selectedChunkId(selectedNode);

  // Auto-scroll the selected window into view on selection change. We seek
  // for the first DOM segment carrying ``data-selected-window="true"``
  // (set during render below) and scroll its container until that segment
  // sits roughly a third of the way down the viewport — close enough to
  // the top to be obviously "the selection" without losing the lead-in
  // context above it.
  useEffect(() => {
    if (!selectedWindow) return;
    const scroller = scrollRef.current;
    if (!scroller) return;
    // rAF so we run after the segment array has been re-rendered with the
    // new data-selected-window attribute.
    const id = window.requestAnimationFrame(() => {
      const target = scroller.querySelector<HTMLElement>('[data-selected-window="true"]');
      if (!target) return;
      const offset = target.offsetTop - scroller.clientHeight / 3;
      scroller.scrollTo({ top: Math.max(0, offset), behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(id);
  }, [selectedWindow?.windowId, selectedWindow?.start, selectedWindow?.end]);

  if (loading && !payload) {
    return (
      <div data-testid="doc-source-loading" className="p-4 font-mono text-[10px] text-zinc-500">
        Loading {docId}…
      </div>
    );
  }
  if (error) {
    return (
      <div data-testid="doc-source-error" className="p-4 font-mono text-[10px] text-red-300">
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

  const chunkStatus = _buildChunkStatusFromEvents(events, docId);

  // Build segment array for rendering: walk fullText and slice by the union
  // of chunk-boundary positions. Each segment carries the chunk it belongs
  // to (for tooltip + highlight) and any mention spans it overlaps.
  const segments = _buildSegments(
    payload.text,
    chunkSpans,
    mentionSpans,
    selectedChunkId,
    chunkStatus,
    packWindows,
    selectedWindow,
  );

  // Minimap: clicking a band scrolls the document text to that chunk's start.
  const totalChars = payload.char_count || 1;
  const onMinimapClick = (chunkStart: number) => {
    const el = scrollRef.current;
    if (!el) return;
    const ratio = chunkStart / totalChars;
    el.scrollTo({ top: el.scrollHeight * ratio, behavior: "smooth" });
  };

  return (
    <div data-testid="doc-source-panel" className="flex flex-col h-full">
      <div
        data-testid="doc-source-header"
        className="sticky top-0 z-10 bg-surface-1/95 backdrop-blur border-b border-white/5 px-3 py-2 font-mono text-[10px] text-zinc-400 flex items-center gap-3"
      >
        <span className="text-zinc-300">document source</span>
        <span className="text-zinc-600">{payload.char_count.toLocaleString()} chars</span>
        <span className="text-zinc-600">·</span>
        <span className="text-zinc-600">{chunkSpans.length} chunks</span>
        <span className="text-zinc-600">·</span>
        <span className="text-zinc-500 text-[9px]">source: {payload.source}</span>
        <span className="ml-auto flex items-center gap-2 text-[9px] text-zinc-500">
          <span className="inline-flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm bg-emerald-500/60" /> accepted
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm bg-amber-500/70" /> pruned
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm bg-zinc-600/50" /> input
          </span>
          <span className="text-zinc-600">·</span>
          <DeepLinkButton testidPrefix="document" panelName="document" />
        </span>
      </div>
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="flex-1 min-h-0 flex">
          {/* Chunk minimap — proportional bands per chunk, color-coded by lifecycle */}
          <div
            className="w-3 flex-shrink-0 flex flex-col bg-surface-1/80 border-r border-white/5"
            title="Chunk minimap"
          >
            {chunkSpans.map((c) => {
              const sel = selectedChunkId === c.chunkId;
              const { bg, label } = _chunkStatusColor(c.chunkId, chunkStatus, sel);
              const heightPct = ((c.end - c.start) / totalChars) * 100;
              return (
                <button
                  type="button"
                  key={c.chunkId}
                  onClick={() => onMinimapClick(c.start)}
                  className={`${bg} hover:brightness-125 transition-all border-b border-black/30 cursor-pointer`}
                  style={{ height: `${heightPct}%`, minHeight: "2px" }}
                  title={`${c.index != null ? `chunk #${c.index}` : c.chunkId} · ${label} · ${c.end - c.start}ch\n${c.preview}`}
                />
              );
            })}
          </div>
          <div
            ref={scrollRef}
            className="flex-1 min-w-0 overflow-y-auto px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-zinc-300"
          >
            {segments.map((seg, i) => {
              const body = seg.chunkStatus ? _chunkBodyStyle(seg.chunkStatus) : null;
              const classes = [
                body ? body.bg : "",
                seg.isChunkStart ? (body?.border ?? "") : "",
                seg.isSelected ? "ring-1 ring-cyan-400" : "",
                // Selected spo_window overlay — saturated so it wins over the
                // pack-window kept band underneath. Box-decoration-clone keeps
                // the rounded corners intact when the run wraps across lines.
                seg.inSelectedWindow
                  ? "bg-cyan-500/30 ring-1 ring-cyan-400/80 rounded-sm box-decoration-clone"
                  : "",
                !seg.inSelectedWindow && seg.packStatus === "kept"
                  ? "bg-emerald-500/15 border-y border-emerald-500/30"
                  : "",
                !seg.inSelectedWindow && seg.packStatus === "pruned"
                  ? "bg-amber-500/15 border-y border-amber-500/30"
                  : "",
                seg.mentionType ? "underline decoration-violet-400 decoration-2" : "",
                seg.isChunkStart ? "pl-1.5" : "",
              ]
                .filter(Boolean)
                .join(" ");
              const tooltip = seg.inSelectedWindow
                ? `selected window · ${seg.selectedWindowId}`
                : seg.packWindowId
                  ? `${seg.packStatus === "kept" ? "kept" : "pruned"} window · ${seg.packWindowId} · ${seg.packMentions}m`
                  : seg.mentionType
                    ? `${seg.mentionType}: ${seg.mentionText}`
                    : seg.chunkLabel
                      ? `${seg.chunkLabel} · ${seg.chunkStatus ?? ""}\n${seg.chunkPreview}`
                      : undefined;
              return (
                <span
                  key={i}
                  className={classes}
                  title={tooltip}
                  data-selected-window={seg.inSelectedWindow ? "true" : undefined}
                  data-mention-source={seg.mentionSource ?? undefined}
                  data-mention-type={seg.mentionType ?? undefined}
                  data-pack-window-status={seg.packStatus ?? undefined}
                >
                  {seg.isChunkStart && body && (
                    <span
                      className={`inline-flex items-center gap-1 align-middle mr-1.5 px-1.5 py-0.5 rounded border text-[9px] font-mono uppercase tracking-wide ${body.chip}`}
                    >
                      <span className={`w-1.5 h-1.5 rounded-full ${body.chipDot}`} />
                      {seg.chunkIndex != null ? `#${seg.chunkIndex}` : "chunk"}
                      <span className="opacity-70">·</span>
                      <span className="opacity-90">{seg.chunkStatus}</span>
                    </span>
                  )}
                  {seg.text}
                </span>
              );
            })}
          </div>
          {/* Right-edge coverage gutter (Gap #6) — mention density + GT
              recall holes + selected-window marker. Renders even when the
              doc has zero consensus mentions (pale zinc track) so its
              presence cues "no coverage on this doc". */}
          <DocCoverageGutter
            totalChars={totalChars}
            consensusSpans={docConsensusSpans}
            gtSpans={docGtSpans}
            selectedWindow={
              selectedWindow ? { start: selectedWindow.start, end: selectedWindow.end } : null
            }
            scrollRef={scrollRef}
          />
        </div>
        {/* Pathology rollup — document-level health signals */}
        <div className="flex-shrink-0 px-3 py-2 bg-surface-1/50 border-t border-white/5 overflow-x-auto">
          <PathologyRollup events={events} docId={docId} />
        </div>
      </div>
    </div>
  );
}

interface RenderSegment {
  text: string;
  chunkId: string | null;
  chunkIndex: number | null;
  chunkLabel: string | null;
  chunkPreview: string | null;
  chunkStatus: ChunkStatusLabel | null;
  isChunkStart: boolean;
  isSelected: boolean;
  mentionType: string | null;
  mentionText: string | null;
  mentionSource: "encoder" | "consensus" | null;
  packWindowId: string | null;
  packStatus: "kept" | "pruned" | null;
  packMentions: number;
  /** True when this segment falls inside the currently-selected
   *  spo_window's char range — drives the saturated overlay + scroll
   *  target tag. */
  inSelectedWindow: boolean;
  selectedWindowId: string | null;
}

function _buildSegments(
  fullText: string,
  chunkSpans: ChunkSpan[],
  mentionSpans: MentionSpan[],
  selectedChunkId: string | null,
  chunkStatus: ChunkStatusMap,
  packWindows: PackWindowOverlay[] = [],
  selectedWindow: SelectedWindow | null = null,
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
  for (const p of packWindows) {
    breaks.add(p.start);
    breaks.add(p.end);
  }
  if (selectedWindow) {
    breaks.add(selectedWindow.start);
    breaks.add(selectedWindow.end);
  }
  const sorted = [...breaks].filter((p) => p >= 0 && p <= fullText.length).sort((a, b) => a - b);
  const segs: RenderSegment[] = [];
  let prevChunkId: string | null = null;
  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i]!;
    const b = sorted[i + 1]!;
    if (a >= b) continue;
    const slice = fullText.slice(a, b);
    if (!slice) continue;
    let chunkId: string | null = null;
    let chunkIndex: number | null = null;
    let chunkLabel: string | null = null;
    let chunkPreview: string | null = null;
    let isSelected = false;
    for (const s of chunkSpans) {
      if (a >= s.start && b <= s.end) {
        chunkId = s.chunkId;
        chunkIndex = s.index;
        chunkLabel = s.index != null ? `chunk #${s.index}` : s.chunkId;
        chunkPreview = s.preview;
        if (selectedChunkId && s.chunkId === selectedChunkId) isSelected = true;
        break;
      }
    }
    let mentionType: string | null = null;
    let mentionText: string | null = null;
    let mentionSource: "encoder" | "consensus" | null = null;
    for (const m of mentionSpans) {
      if (a >= m.start && b <= m.end) {
        mentionType = m.type;
        mentionText = m.text;
        mentionSource = m.source ?? null;
        break;
      }
    }
    let packWindowId: string | null = null;
    let packStatus: "kept" | "pruned" | null = null;
    let packMentions = 0;
    for (const p of packWindows) {
      if (a >= p.start && b <= p.end) {
        packWindowId = p.windowId;
        packStatus = p.status;
        packMentions = p.mentionCount;
        break;
      }
    }
    const status = chunkId ? _chunkStatusColor(chunkId, chunkStatus, isSelected).label : null;
    const isChunkStart = chunkId !== null && chunkId !== prevChunkId;
    prevChunkId = chunkId;
    const inSelectedWindow =
      selectedWindow != null && a >= selectedWindow.start && b <= selectedWindow.end;
    segs.push({
      text: slice,
      chunkId,
      chunkIndex,
      chunkLabel,
      chunkPreview,
      chunkStatus: status,
      isChunkStart,
      isSelected,
      mentionType,
      mentionText,
      mentionSource,
      packWindowId,
      packStatus,
      packMentions,
      inSelectedWindow,
      selectedWindowId: inSelectedWindow ? (selectedWindow?.windowId ?? null) : null,
    });
  }
  return segs;
}
