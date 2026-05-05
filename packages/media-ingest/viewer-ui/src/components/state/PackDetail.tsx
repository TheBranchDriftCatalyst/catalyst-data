/**
 * PackDetail — bottom-pane content when the user clicks the `pack` node.
 * Shows the cluster → evidence-window mapping and any density-pruned
 * windows (from the new evidence_window_pruned events).
 *
 * Also renders the twin threshold histograms above the kept/pruned
 * tables (Gap #4 from data-scientist-gaps.md). The histograms hold a
 * single mutually-exclusive `activeFilter` covering both axes — clicking
 * a bar dims rows in the kept + pruned tables below that don't fall in
 * the selected bin. Rows are dimmed (`opacity-30`) rather than dropped
 * so the user keeps a stable count.
 */

import { useMemo, useState } from "react";

import type { RunEvent } from "@/types/benchmark";
import { useTrendData } from "@/hooks/useTrendData";

import {
  PackThresholdHistograms,
  type PackHistFilter,
  type PackWindowRecord,
} from "./PackThresholdHistograms";
import { TrendSparkline } from "../TrendSparkline";

interface Props {
  events: RunEvent[];
  docId: string;
  /** Active run id — required for the trend sparkline to highlight the
   *  current run dot. Optional (defaults null) so older callers don't
   *  break. */
  runId?: string | null;
  /** Gap #8 — selection-preserving run jump from the trend sparkline. */
  onJumpRun?: (runId: string) => void;
}

interface PackEvent {
  windowCount: number;
  prunedCount: number;
  totalTokens: number;
  meanTokensPerWindow: number;
  contextTokens: number;
  pruneMin: number;
  pruneMaxCharsPerMention: number;
  keptWindows: KeptRow[];
}

interface KeptRow {
  windowId: string;
  clusterId: string;
  mentionCount: number;
  charCount: number;
  charsPerMention: number | null;
  docCharStart: number | null;
  docCharEnd: number | null;
}

interface PrunedRow {
  windowId: string;
  clusterId: string;
  mentionCount: number;
  charCount: number;
  charsPerMention: number | null;
  reason: string;
}

// Bin-edge table for chars_per_mention — must match
// PackThresholdHistograms.CPM_BIN_EDGES. Duplicated here so the dimming
// logic stays self-contained without importing the constant. Keep in
// sync if either file changes.
const CPM_BIN_EDGES: number[] = [0, 50, 100, 200, 400, 800, 1600, Infinity];

function cpmBinIndex(value: number): number {
  for (let i = 0; i < CPM_BIN_EDGES.length - 1; i += 1) {
    const lower = CPM_BIN_EDGES[i] ?? 0;
    const upper = CPM_BIN_EDGES[i + 1] ?? Infinity;
    if (value >= lower && value < upper) return i;
  }
  return CPM_BIN_EDGES.length - 2;
}

export function PackDetail({ events, docId, runId, onJumpRun }: Props) {
  const [activeFilter, setActiveFilter] = useState<PackHistFilter | null>(null);
  // Gap #8 — last-10-runs trend for kept/pruned ratio. Higher is better
  // (more retention = less aggressive pruning).
  const { points: trendPoints } = useTrendData({
    axis: "doc",
    metric: "pack_kept_pruned_ratio",
    docId,
  });

  const summary = useMemo<PackEvent | null>(() => {
    const e = events.find(
      (ev) =>
        ev.node_name === "pack_evidence" &&
        ev.status === "completed" &&
        (ev.doc_id === docId || ev.chunk_id?.startsWith(`${docId}:`)),
    );
    if (!e) return null;
    const d = (e.details ?? {}) as Record<string, unknown>;
    const keptRaw = (d.kept_windows as Array<Record<string, unknown>>) ?? [];
    const kept: KeptRow[] = keptRaw.map((w) => {
      const mentionCount = (w.mention_count as number) ?? 0;
      const charCount = (w.char_count as number) ?? 0;
      const cpmRaw = w.chars_per_mention;
      const charsPerMention =
        typeof cpmRaw === "number" ? cpmRaw : mentionCount > 0 ? charCount / mentionCount : null;
      return {
        windowId: (w.window_id as string) ?? "",
        clusterId: (w.cluster_id as string) ?? "",
        mentionCount,
        charCount,
        charsPerMention,
        docCharStart: typeof w.doc_char_start === "number" ? (w.doc_char_start as number) : null,
        docCharEnd: typeof w.doc_char_end === "number" ? (w.doc_char_end as number) : null,
      };
    });
    return {
      windowCount: (d.window_count as number) ?? 0,
      prunedCount: (d.pruned_count as number) ?? 0,
      totalTokens: (d.total_tokens as number) ?? 0,
      meanTokensPerWindow: (d.mean_tokens_per_window as number) ?? 0,
      contextTokens: (d.context_tokens as number) ?? 0,
      pruneMin: (d.prune_min_mentions as number) ?? 0,
      pruneMaxCharsPerMention: (d.prune_max_chars_per_mention as number) ?? 0,
      keptWindows: kept,
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

  // Build the histogram input arrays — these wrap kept/pruned with the
  // PackWindowRecord shape the chart expects.
  const histKept: PackWindowRecord[] = useMemo(
    () =>
      (summary?.keptWindows ?? []).map((w) => ({
        window_id: w.windowId,
        cluster_id: w.clusterId,
        mention_count: w.mentionCount,
        char_count: w.charCount,
        chars_per_mention: w.charsPerMention,
      })),
    [summary],
  );
  const histPruned: PackWindowRecord[] = useMemo(
    () =>
      pruned.map((p) => ({
        window_id: p.windowId,
        cluster_id: p.clusterId,
        mention_count: p.mentionCount,
        char_count: p.charCount,
        chars_per_mention: p.charsPerMention,
        reason: p.reason,
      })),
    [pruned],
  );

  /**
   * Decide whether a row matches the active histogram filter. Returns
   * `true` (match → full opacity) when no filter is active.
   */
  const rowMatchesFilter = (mentionCount: number, charsPerMention: number | null): boolean => {
    if (!activeFilter) return true;
    if (activeFilter.axis === "mention_count") {
      return mentionCount === activeFilter.binIdx;
    }
    if (charsPerMention == null) return false;
    return cpmBinIndex(charsPerMention) === activeFilter.binIdx;
  };

  if (!summary && pruned.length === 0) {
    return (
      <div className="p-4 font-mono text-[10px] text-zinc-500">
        Pack stage hasn't completed yet for {docId}.
      </div>
    );
  }

  // Use existing thresholds (default to 0 when no summary, e.g. only
  // pruned events arrived but pack_evidence never completed).
  const pruneMin = summary?.pruneMin ?? 0;
  const pruneMaxCpm = summary?.pruneMaxCharsPerMention ?? 0;

  return (
    <div data-testid="pack-detail" className="p-3 font-mono text-[11px] space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="text-zinc-500 text-[10px]">pack — {docId}</div>
        <TrendSparkline
          points={trendPoints}
          metric="pack_kept_pruned_ratio"
          currentRunId={runId ?? null}
          onSelectRun={(id) => onJumpRun?.(id)}
          trend="up-good"
        />
      </div>
      {summary && (
        <div className="flex flex-wrap gap-3 text-zinc-400">
          <span>
            kept:{" "}
            <span data-testid="pack-kept-count" className="text-emerald-300">
              {summary.windowCount}
            </span>
          </span>
          <span>
            pruned:{" "}
            <span
              data-testid="pack-pruned-count"
              className={summary.prunedCount > 0 ? "text-amber-300" : "text-zinc-500"}
            >
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
      {/* Threshold counterfactual histograms — render above the tables. */}
      {summary && (
        <PackThresholdHistograms
          kept={histKept}
          pruned={histPruned}
          pruneMinMentions={pruneMin}
          pruneMaxCharsPerMention={pruneMaxCpm}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
        />
      )}
      {summary && summary.keptWindows.length > 0 && (
        <div className="space-y-1">
          <div className="text-zinc-500 uppercase text-[9px] tracking-wide">
            kept windows ({summary.keptWindows.length})
          </div>
          <div className="rounded border border-emerald-500/10 max-h-48 overflow-y-auto">
            <table className="w-full text-[10px]">
              <thead className="sticky top-0 bg-surface-1/80 backdrop-blur">
                <tr className="text-zinc-500 text-left">
                  <th className="px-2 py-1 font-normal">window</th>
                  <th className="px-2 py-1 font-normal">cluster</th>
                  <th className="px-2 py-1 font-normal text-right">mentions</th>
                  <th className="px-2 py-1 font-normal text-right">chars</th>
                  <th className="px-2 py-1 font-normal text-right">doc range</th>
                </tr>
              </thead>
              <tbody>
                {summary.keptWindows.map((w) => {
                  const matches = rowMatchesFilter(w.mentionCount, w.charsPerMention);
                  return (
                    <tr
                      key={w.windowId}
                      data-testid={`pack-kept-row-${w.windowId}`}
                      className={
                        "border-t border-emerald-500/10 transition-opacity " +
                        (matches ? "" : "opacity-30")
                      }
                    >
                      <td className="px-2 py-0.5 text-emerald-200 truncate">{w.windowId}</td>
                      <td className="px-2 py-0.5 text-zinc-400 truncate">{w.clusterId}</td>
                      <td className="px-2 py-0.5 text-right text-zinc-300">{w.mentionCount}</td>
                      <td className="px-2 py-0.5 text-right text-zinc-500">{w.charCount}</td>
                      <td className="px-2 py-0.5 text-right text-zinc-500">
                        {w.docCharStart != null && w.docCharEnd != null
                          ? `${w.docCharStart}–${w.docCharEnd}`
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {pruned.length > 0 && (
        <div className="space-y-1">
          <div className="text-zinc-500 uppercase text-[9px] tracking-wide">
            pruned windows ({pruned.length})
          </div>
          <div className="space-y-1">
            {pruned.map((p) => {
              const matches = rowMatchesFilter(p.mentionCount, p.charsPerMention);
              return (
                <div
                  key={p.windowId}
                  data-testid={`pack-pruned-row-${p.windowId}`}
                  className={
                    "px-2 py-1 rounded bg-amber-500/5 border border-amber-500/20 flex items-center gap-2 text-[10px] transition-opacity " +
                    (matches ? "" : "opacity-30")
                  }
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
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
