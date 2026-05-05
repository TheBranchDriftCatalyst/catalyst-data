/**
 * ChunksDetail — bottom-pane content when the user clicks the `chunks`
 * node in the pipeline graph. Lists every pre-NER input chunk loaded for
 * the doc with its chunking metadata (strategy, char range, speaker,
 * temporal bounds) so the operator can see how the doc was split before
 * the encoders saw it.
 */

import { useMemo, useState } from "react";

import type { RunEvent } from "@/types/benchmark";
import { DeepLinkButton } from "./DeepLinkButton";

interface Props {
  events: RunEvent[];
  docId: string;
}

interface ChunkRow {
  chunkId: string;
  index: number | null;
  charCount: number;
  domain: string | null;
  speaker: string | null;
  tStartMs: number | null;
  tEndMs: number | null;
  strategy: string | null;
  textPreview: string;
  fullText: string;
  metadata: Record<string, unknown>;
}

function _isInputChunk(cid: string): boolean {
  return (
    !cid.includes(":_ner_") &&
    !cid.endsWith(":_consensus") &&
    !cid.includes(":win-") &&
    !cid.endsWith(":_doc_ensemble")
  );
}

export function ChunksDetail({ events, docId }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const rows = useMemo<ChunkRow[]>(() => {
    // Dedupe by chunk_id — DuckDB hive-partitioned audit log preserves events
    // across re-runs, so the same chunk_loaded gets replayed N times. The
    // upstream emit_chunk_text is idempotent per-process but not per-store.
    const byId = new Map<string, ChunkRow>();
    for (const e of events) {
      if (e.node_name !== "chunk_loaded" || !e.chunk_id) continue;
      if (!e.chunk_id.startsWith(`${docId}:`)) continue;
      if (!_isInputChunk(e.chunk_id)) continue;
      if (byId.has(e.chunk_id)) continue;
      const d = (e.details ?? {}) as {
        text?: string;
        char_count?: number;
        domain?: string | null;
        speaker_label?: string | null;
        temporal_start_ms?: number | null;
        temporal_end_ms?: number | null;
        chunk_index?: number | null;
        chunk_metadata?: Record<string, unknown>;
      };
      const meta = (d.chunk_metadata ?? {}) as Record<string, unknown>;
      const text = d.text ?? "";
      byId.set(e.chunk_id, {
        chunkId: e.chunk_id,
        index: d.chunk_index ?? null,
        charCount: d.char_count ?? text.length,
        domain: d.domain ?? null,
        speaker: d.speaker_label ?? null,
        tStartMs: d.temporal_start_ms ?? null,
        tEndMs: d.temporal_end_ms ?? null,
        strategy: (meta["strategy"] as string) ?? null,
        textPreview: text.slice(0, 200),
        fullText: text,
        metadata: meta,
      });
    }
    const out = [...byId.values()];
    out.sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
    return out;
  }, [events, docId]);

  if (rows.length === 0) {
    return (
      <div className="p-4 font-mono text-[10px] text-zinc-500">
        No input chunks observed yet for {docId}. The encoders may have run on the doc-level text
        directly without per-chunk chunk_loaded events.
      </div>
    );
  }

  // Aggregate: strategies seen, total chars, total chunks
  const totalChars = rows.reduce((s, r) => s + r.charCount, 0);
  const strategies = new Map<string, number>();
  for (const r of rows) {
    if (r.strategy) strategies.set(r.strategy, (strategies.get(r.strategy) ?? 0) + 1);
  }

  return (
    <div data-testid="chunks-detail" className="p-3 font-mono text-[11px] space-y-2">
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-zinc-300 text-[10px]">chunks</span>
        <DeepLinkButton testidPrefix="chunks" panelName="chunks" />
      </div>
      <div className="flex items-center gap-3 text-zinc-400">
        <span className="text-zinc-300">{rows.length} input chunks</span>
        <span className="text-zinc-600">·</span>
        <span>{totalChars.toLocaleString()} chars total</span>
        {strategies.size > 0 && (
          <>
            <span className="text-zinc-600">·</span>
            <span>strategy:</span>
            {[...strategies.entries()].map(([s, n]) => (
              <span key={s} className="px-1.5 py-0.5 rounded bg-zinc-700/40 text-zinc-300">
                {s} ×{n}
              </span>
            ))}
          </>
        )}
      </div>
      <div className="space-y-1">
        {rows.map((r) => {
          const isOpen = expanded === r.chunkId;
          return (
            <div
              key={r.chunkId}
              data-testid={`chunk-row-${r.chunkId}`}
              data-expanded={isOpen ? "true" : "false"}
              className="rounded border border-white/5 bg-white/[0.02]"
            >
              <button
                type="button"
                onClick={() => setExpanded(isOpen ? null : r.chunkId)}
                className="w-full text-left px-2 py-1 flex items-center gap-2 hover:bg-white/[0.03]"
              >
                <span className="text-zinc-500 w-8">#{r.index ?? "?"}</span>
                <span className="text-zinc-400 w-16">{r.charCount}ch</span>
                {r.speaker && (
                  <span className="px-1 rounded bg-violet-500/20 text-violet-200 text-[9px]">
                    {r.speaker}
                  </span>
                )}
                {r.tStartMs != null && r.tEndMs != null && (
                  <span className="text-zinc-500 text-[9px]">
                    {(r.tStartMs / 1000).toFixed(1)}–{(r.tEndMs / 1000).toFixed(1)}s
                  </span>
                )}
                <span className="flex-1 truncate text-zinc-300">
                  {r.textPreview}
                  {r.fullText.length > 200 ? "…" : ""}
                </span>
                <span className="text-zinc-600">{isOpen ? "▾" : "▸"}</span>
              </button>
              {isOpen && (
                <div className="px-2 py-2 border-t border-white/5 space-y-2">
                  <div className="text-[9px] text-zinc-500 uppercase tracking-wide">full text</div>
                  <div
                    data-testid="chunk-row-fulltext"
                    className="whitespace-pre-wrap text-zinc-200 text-[10.5px] max-h-48 overflow-y-auto"
                  >
                    {r.fullText}
                  </div>
                  {Object.keys(r.metadata).length > 0 && (
                    <>
                      <div className="text-[9px] text-zinc-500 uppercase tracking-wide">
                        chunk_metadata
                      </div>
                      <div className="space-y-0.5 text-[10px]">
                        {Object.entries(r.metadata).map(([k, v]) => (
                          <div key={k} className="flex gap-2">
                            <span className="text-zinc-500 w-24 truncate">{k}</span>
                            <span className="text-zinc-300 truncate">
                              {typeof v === "object" ? JSON.stringify(v) : String(v)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
