/**
 * Left rail of the StateInspector — every (model × chunk_id) pair
 * for which we've seen any event, grouped by model. Click selects.
 */

import { useMemo } from "react";

import type { RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  selectedModel: string | null;
  selectedChunk: string | null;
  onSelect: (model: string, chunkId: string) => void;
}

interface ChunkSummary {
  chunkId: string;
  docId: string | null;
  mentionCount: number | null;
  propositionCount: number | null;
  hasError: boolean;
  retries: number;
}

export function ChunkRail({ events, selectedModel, selectedChunk, onSelect }: Props) {
  const grouped = useMemo(() => {
    const out = new Map<string, Map<string, ChunkSummary>>();
    for (const e of events) {
      if (!e.chunk_id) continue;
      const model = e.model ?? "—";
      let bucket = out.get(model);
      if (!bucket) {
        bucket = new Map();
        out.set(model, bucket);
      }
      let summary = bucket.get(e.chunk_id);
      if (!summary) {
        summary = {
          chunkId: e.chunk_id,
          docId: e.doc_id,
          mentionCount: null,
          propositionCount: null,
          hasError: false,
          retries: 0,
        };
        bucket.set(e.chunk_id, summary);
      }
      if (e.node_name === "chunk_extracted") {
        const d = e.details as Record<string, unknown>;
        summary.mentionCount = (d.mention_count as number) ?? null;
        summary.propositionCount = (d.proposition_count as number) ?? null;
      }
      if (e.status === "error" || e.status === "failed") summary.hasError = true;
      if (e.retry_count != null && e.retry_count > summary.retries) {
        summary.retries = e.retry_count;
      }
    }
    return out;
  }, [events]);

  // Models with at least one chunk event, alphabetical.
  const models = useMemo(() => [...grouped.keys()].filter((m) => m !== "—").sort(), [grouped]);

  if (models.length === 0) {
    return (
      <div className="p-4 font-mono text-xs text-zinc-600">
        No chunks observed yet. The harness emits a `chunk_loaded` event when each chunk first
        enters the graph.
      </div>
    );
  }

  return (
    <div className="py-2">
      {models.map((model) => {
        const chunks = [...(grouped.get(model)?.values() ?? [])].sort((a, b) =>
          a.chunkId.localeCompare(b.chunkId),
        );
        const expanded = model === selectedModel;
        return (
          <div key={model} className="mb-1">
            <button
              type="button"
              onClick={() => {
                if (chunks.length > 0) onSelect(model, chunks[0]!.chunkId);
              }}
              className={`w-full text-left px-3 py-1.5 font-mono text-[11px] flex items-center gap-2 ${
                expanded ? "bg-white/[0.04] text-zinc-200" : "text-zinc-400 hover:bg-white/[0.02]"
              }`}
            >
              <span className="text-zinc-500">{expanded ? "▾" : "▸"}</span>
              <span className="flex-1 truncate">{model}</span>
              <span className="text-zinc-600">{chunks.length}</span>
            </button>
            {expanded && (
              <div className="border-l border-white/5 ml-3">
                {chunks.map((c) => {
                  const isSel = selectedChunk === c.chunkId && selectedModel === model;
                  return (
                    <button
                      key={c.chunkId}
                      type="button"
                      onClick={() => onSelect(model, c.chunkId)}
                      className={`w-full text-left px-3 py-1 font-mono text-[10px] flex items-center gap-2 ${
                        isSel
                          ? "bg-cyan-500/10 text-cyan-200"
                          : "text-zinc-500 hover:bg-white/[0.02]"
                      }`}
                    >
                      <span className="flex-1 truncate" title={c.chunkId}>
                        {c.chunkId.split(":").slice(-2).join(":")}
                      </span>
                      {c.mentionCount != null && (
                        <span className="text-zinc-600">
                          {c.mentionCount}m·{c.propositionCount ?? 0}p
                        </span>
                      )}
                      {c.retries > 0 && <span className="text-amber-400">↻{c.retries}</span>}
                      {c.hasError && <span className="text-red-400">!</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
