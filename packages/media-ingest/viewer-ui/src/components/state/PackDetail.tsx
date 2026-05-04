/**
 * PackDetail — bottom-pane content when the user clicks the `pack` node.
 * Shows the cluster → evidence-window mapping and any density-pruned
 * windows (from the new evidence_window_pruned events).
 */

import { useMemo } from "react";

import type { RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  docId: string;
}

interface PackEvent {
  windowCount: number;
  prunedCount: number;
  totalTokens: number;
  meanTokensPerWindow: number;
  contextTokens: number;
  pruneMin: number;
  pruneMaxCharsPerMention: number;
}

interface PrunedRow {
  windowId: string;
  clusterId: string;
  mentionCount: number;
  charCount: number;
  charsPerMention: number | null;
  reason: string;
}

export function PackDetail({ events, docId }: Props) {
  const summary = useMemo<PackEvent | null>(() => {
    const e = events.find(
      (ev) =>
        ev.node_name === "pack_evidence" &&
        ev.status === "completed" &&
        (ev.doc_id === docId || ev.chunk_id?.startsWith(`${docId}:`)),
    );
    if (!e) return null;
    const d = (e.details ?? {}) as Record<string, number>;
    return {
      windowCount: d.window_count ?? 0,
      prunedCount: d.pruned_count ?? 0,
      totalTokens: d.total_tokens ?? 0,
      meanTokensPerWindow: d.mean_tokens_per_window ?? 0,
      contextTokens: d.context_tokens ?? 0,
      pruneMin: d.prune_min_mentions ?? 0,
      pruneMaxCharsPerMention: d.prune_max_chars_per_mention ?? 0,
    };
  }, [events, docId]);

  const pruned = useMemo<PrunedRow[]>(() => {
    const out: PrunedRow[] = [];
    for (const e of events) {
      if (e.node_name !== "evidence_window_pruned") continue;
      if (e.doc_id !== docId && !e.chunk_id?.startsWith(`${docId}:`)) continue;
      const d = (e.details ?? {}) as {
        window_id?: string;
        cluster_id?: string;
        mention_count?: number;
        char_count?: number;
        chars_per_mention?: number | null;
        reason?: string;
      };
      out.push({
        windowId: d.window_id ?? "",
        clusterId: d.cluster_id ?? "",
        mentionCount: d.mention_count ?? 0,
        charCount: d.char_count ?? 0,
        charsPerMention: d.chars_per_mention ?? null,
        reason: d.reason ?? "?",
      });
    }
    return out;
  }, [events, docId]);

  if (!summary && pruned.length === 0) {
    return (
      <div className="p-4 font-mono text-[10px] text-zinc-500">
        Pack stage hasn't completed yet for {docId}.
      </div>
    );
  }

  return (
    <div className="p-3 font-mono text-[11px] space-y-3">
      {summary && (
        <div className="flex flex-wrap gap-3 text-zinc-400">
          <span>
            kept: <span className="text-emerald-300">{summary.windowCount}</span>
          </span>
          <span>
            pruned:{" "}
            <span className={summary.prunedCount > 0 ? "text-amber-300" : "text-zinc-500"}>
              {summary.prunedCount}
            </span>
          </span>
          <span className="text-zinc-600">·</span>
          <span>
            total ≈ <span className="text-zinc-200">{summary.totalTokens}</span> tok
          </span>
          <span>
            mean ≈ <span className="text-zinc-200">{summary.meanTokensPerWindow}</span> tok/window
          </span>
          <span>
            ctx <span className="text-zinc-200">{summary.contextTokens}</span>
          </span>
          <span className="text-zinc-600">·</span>
          <span className="text-zinc-500 text-[9px]">
            thresholds: min_mentions={summary.pruneMin}, max_chars_per_mention=
            {summary.pruneMaxCharsPerMention}
          </span>
        </div>
      )}
      {pruned.length > 0 && (
        <div className="space-y-1">
          <div className="text-zinc-500 uppercase text-[9px] tracking-wide">
            pruned windows ({pruned.length})
          </div>
          <div className="space-y-1">
            {pruned.map((p) => (
              <div
                key={p.windowId}
                className="px-2 py-1 rounded bg-amber-500/5 border border-amber-500/20 flex items-center gap-2 text-[10px]"
              >
                <span className="text-amber-300 w-20 truncate">{p.windowId}</span>
                <span className="text-zinc-500 w-24 truncate">cluster {p.clusterId}</span>
                <span className="text-zinc-400">{p.mentionCount}m</span>
                <span className="text-zinc-400">{p.charCount}ch</span>
                {p.charsPerMention != null && (
                  <span className="text-zinc-500 text-[9px]">
                    {p.charsPerMention.toFixed(0)} ch/m
                  </span>
                )}
                <span className="flex-1 truncate text-amber-200">{p.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
