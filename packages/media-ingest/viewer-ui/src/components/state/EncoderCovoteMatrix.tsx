/**
 * EncoderCovoteMatrix — pairwise encoder agreement view for the
 * ConsensusDetail panel (Gap #2 from the data-scientist tour).
 *
 * Renders an N×N grid where:
 *   - The diagonal shows ``[lone-vote count]`` — mentions where ONLY that
 *     encoder voted (rendered in zinc-300 brackets to distinguish from
 *     the off-diagonal float values).
 *   - The upper triangle shows Jaccard ``|A ∩ B| / |A ∪ B|`` over the
 *     mention set, formatted to 2 decimals. Background cyan ramps with
 *     value (0.0 → cyan-50, ~0.4 → cyan-300, ≥0.7 → cyan-500).
 *   - The lower triangle is blank — the matrix is symmetric so we don't
 *     duplicate ink.
 *
 * Click a cell to filter the accepted-mentions list:
 *   - Off-diagonal → ``{type: "pair", encoders: [A, B]}`` — dim rows
 *     whose source_models doesn't include both encoders.
 *   - Diagonal → ``{type: "lone", encoders: [X]}`` — only undimmed when
 *     source_models == [X].
 *   - Click again to clear.
 *
 * Filters live in the parent (ConsensusDetail) so the dim-state can be
 * applied to the existing accepted MentionTable rows.
 *
 * Sizing rules (from the spec):
 *   - ≥3 encoders: matrix expanded by default.
 *   - 2 encoders: render a single inline ``A ∩ B = 0.78 (lone: 33 / 4)``
 *     line — no interactive filter (sample too thin).
 *   - <2 encoders: nothing.
 *   - <5 total accepted+rejected mentions: render a "low sample,
 *     agreement noisy" caption regardless of size.
 *
 * Mention key
 * -----------
 * The vote-attribution key is ``${text}|${canonical_type}|${span_start ?? ""}``.
 * In current bench events ``span_start`` is not part of the audit-event
 * details (consensus.py emits canonical_text + canonical_type only), so
 * the trailing slot is empty in practice — that's fine because consensus
 * has already deduped clusters by canonical-text + 50% span overlap.
 * Including the slot keeps us forward-compatible if the audit shape
 * grows the field later.
 */

import { useMemo } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@thebranchdriftcatalyst/catalyst-ui";

import type { MentionDecisionDetails, MentionRejectedDetails } from "@/types/benchmark";

// Reuse the page-wide tooltip surface treatment so the hover doesn't
// look transparent against the panel background. Mirrors BenchmarkRunner
// (see TOOLTIP_CLS at the top of pages/BenchmarkRunner.tsx).
const TOOLTIP_CLS =
  "z-50 max-w-sm rounded-md border border-white/10 bg-surface-1 text-zinc-100 px-3 py-2 shadow-xl text-[11px] leading-relaxed font-mono whitespace-pre-line";

export type EncoderCovoteFilter =
  | { type: "pair"; encoders: [string, string] }
  | { type: "lone"; encoders: [string] };

interface Props {
  encoders: string[];
  accepted: MentionDecisionDetails[];
  rejected: MentionRejectedDetails[];
  mode: "accepted" | "all";
  activeFilter: EncoderCovoteFilter | null;
  onFilterChange: (next: EncoderCovoteFilter | null) => void;
  onModeChange: (next: "accepted" | "all") => void;
}

interface MentionCell {
  // canonical_type may be missing on rejected events — fall back to "?"
  // so two rejected mentions with the same text but different (missing)
  // type still cluster together rather than fragmenting the matrix.
  text: string;
  type: string;
  spanStart: string;
  voters: Set<string>;
}

function mentionKey(text: string, type: string, spanStart: number | null | undefined): string {
  return `${text}|${type}|${spanStart ?? ""}`;
}

/** Map jaccard value to a cyan background utility. The ramp is intentionally
 *  coarse — the operator's job is "find the bright cells", not read tenths. */
function jaccardBgClass(j: number): string {
  if (j >= 0.7) return "bg-cyan-500/60 text-zinc-950";
  if (j >= 0.5) return "bg-cyan-500/40 text-zinc-100";
  if (j >= 0.3) return "bg-cyan-400/30 text-zinc-100";
  if (j >= 0.15) return "bg-cyan-300/20 text-zinc-200";
  if (j > 0) return "bg-cyan-200/10 text-zinc-300";
  return "bg-white/[0.02] text-zinc-500";
}

interface PairStats {
  intersection: number;
  union: number;
  jaccard: number;
  onlyA: number;
  onlyB: number;
}

interface MatrixStats {
  loneByEncoder: Record<string, number>;
  pairByKey: Record<string, PairStats>;
  totalMentions: number;
}

function computeMatrix(
  encoders: string[],
  accepted: MentionDecisionDetails[],
  rejected: MentionRejectedDetails[],
  mode: "accepted" | "all",
): MatrixStats {
  // Build the canonical mention map. Each entry's voters set lists which
  // encoders contributed to the cluster (per source_models on the audit
  // event). For rejected mentions in "all" mode we read source_models if
  // present — current production schema doesn't ship it on rejected
  // events (see consensus.py L293), so this is best-effort and only
  // strengthens the matrix on richer fixtures.
  const byKey = new Map<string, MentionCell>();

  for (const m of accepted) {
    const k = mentionKey(
      m.text,
      m.canonical_type,
      (m as unknown as { span_start?: number | null }).span_start,
    );
    let cell = byKey.get(k);
    if (!cell) {
      cell = { text: m.text, type: m.canonical_type, spanStart: "", voters: new Set() };
      byKey.set(k, cell);
    }
    for (const enc of m.source_models ?? []) cell.voters.add(enc);
  }

  if (mode === "all") {
    for (const m of rejected) {
      const dx = m as unknown as {
        canonical_type?: string;
        span_start?: number | null;
        source_models?: string[];
      };
      const sm = dx.source_models ?? [];
      // Skip rejected entries that don't carry source_models — without
      // attribution we can't add anything to the matrix without
      // hallucinating votes.
      if (sm.length === 0) continue;
      const k = mentionKey(m.text, dx.canonical_type ?? "?", dx.span_start);
      let cell = byKey.get(k);
      if (!cell) {
        cell = {
          text: m.text,
          type: dx.canonical_type ?? "?",
          spanStart: "",
          voters: new Set(),
        };
        byKey.set(k, cell);
      }
      for (const enc of sm) cell.voters.add(enc);
    }
  }

  // Per-encoder lone count (mentions where the voter set is exactly {enc}).
  const loneByEncoder: Record<string, number> = Object.fromEntries(encoders.map((e) => [e, 0]));
  for (const cell of byKey.values()) {
    if (cell.voters.size === 1) {
      const [only] = [...cell.voters];
      if (only !== undefined && only in loneByEncoder) {
        loneByEncoder[only] = (loneByEncoder[only] ?? 0) + 1;
      }
    }
  }

  // Pairwise stats. We compute |A ∩ B|, |A ∪ B|, Jaccard, and the two
  // "only A" / "only B" counts for the tooltip.
  const pairByKey: Record<string, PairStats> = {};
  for (let i = 0; i < encoders.length; i += 1) {
    for (let j = i + 1; j < encoders.length; j += 1) {
      const a = encoders[i];
      const b = encoders[j];
      if (a === undefined || b === undefined) continue;
      let inter = 0;
      let union = 0;
      let onlyA = 0;
      let onlyB = 0;
      for (const cell of byKey.values()) {
        const hasA = cell.voters.has(a);
        const hasB = cell.voters.has(b);
        if (hasA && hasB) {
          inter += 1;
          union += 1;
        } else if (hasA) {
          onlyA += 1;
          union += 1;
        } else if (hasB) {
          onlyB += 1;
          union += 1;
        }
      }
      const jaccard = union > 0 ? inter / union : 0;
      pairByKey[`${a}__${b}`] = { intersection: inter, union, jaccard, onlyA, onlyB };
    }
  }

  return { loneByEncoder, pairByKey, totalMentions: byKey.size };
}

function pairKey(a: string, b: string): string {
  // Stable order matches computeMatrix's i<j iteration so lookup is deterministic.
  return `${a}__${b}`;
}

function isFilterPair(filter: EncoderCovoteFilter | null, a: string, b: string): boolean {
  if (!filter || filter.type !== "pair") return false;
  const [fa, fb] = filter.encoders;
  return (fa === a && fb === b) || (fa === b && fb === a);
}

function isFilterLone(filter: EncoderCovoteFilter | null, a: string): boolean {
  return filter?.type === "lone" && filter.encoders[0] === a;
}

export function EncoderCovoteMatrix({
  encoders,
  accepted,
  rejected,
  mode,
  activeFilter,
  onFilterChange,
  onModeChange,
}: Props) {
  const stats = useMemo(
    () => computeMatrix(encoders, accepted, rejected, mode),
    [encoders, accepted, rejected, mode],
  );

  if (encoders.length < 2) return null;

  const lowSample = accepted.length + rejected.length < 5;

  // 2 encoders → single inline summary line, no interactive grid.
  if (encoders.length === 2) {
    const [a, b] = encoders;
    if (a === undefined || b === undefined) return null;
    const p = stats.pairByKey[pairKey(a, b)];
    if (!p) return null;
    return (
      <div data-testid="encoder-covote-matrix" className="font-mono text-[10.5px] text-zinc-400">
        <span className="text-zinc-200">{a}</span>
        <span className="text-zinc-600"> ∩ </span>
        <span className="text-zinc-200">{b}</span>
        <span className="text-zinc-600"> = </span>
        <span className="text-cyan-300">{p.jaccard.toFixed(2)}</span>
        <span className="text-zinc-600">
          {" "}
          (lone: {stats.loneByEncoder[a] ?? 0} / {stats.loneByEncoder[b] ?? 0})
        </span>
        {lowSample && (
          <span className="ml-2 text-zinc-600 italic">low sample, agreement noisy</span>
        )}
      </div>
    );
  }

  // 3+ encoders → full interactive matrix.
  const headerCellCls =
    "px-1.5 py-0.5 font-mono text-[9px] text-zinc-400 text-left whitespace-nowrap";
  const baseCellCls =
    "border border-white/[0.04] px-1.5 py-1 font-mono text-[10px] text-center cursor-pointer transition-colors min-w-[44px]";

  return (
    <div data-testid="encoder-covote-matrix" className="space-y-2">
      {/* Mode toggle pill */}
      <div className="flex items-center gap-2 text-[10px] font-mono">
        <span className="text-zinc-600 uppercase tracking-wide">scope:</span>
        {(["accepted", "all"] as const).map((opt) => (
          <button
            key={opt}
            type="button"
            data-testid={`encoder-covote-mode-${opt}`}
            onClick={() => onModeChange(opt)}
            className={`px-1.5 py-0.5 rounded transition-colors ${
              mode === opt ? "bg-cyan-500/20 text-cyan-300" : "text-zinc-600 hover:text-zinc-400"
            }`}
          >
            {opt === "all" ? "accepted+rejected" : "accepted"}
          </button>
        ))}
        {lowSample && (
          <span className="text-amber-400/80 italic">
            low sample ({stats.totalMentions}), agreement noisy
          </span>
        )}
      </div>

      {/* Active filter chip + clear */}
      {activeFilter && (
        <div className="flex items-center gap-2 text-[10px] font-mono">
          <span
            data-testid="encoder-covote-active-filter"
            className="px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30"
          >
            {activeFilter.type === "pair"
              ? `pair: ${activeFilter.encoders[0]} ∩ ${activeFilter.encoders[1]}`
              : `lone: ${activeFilter.encoders[0]}`}
          </span>
          <button
            type="button"
            data-testid="encoder-covote-clear"
            onClick={() => onFilterChange(null)}
            className="text-zinc-500 hover:text-zinc-200 underline-offset-2 hover:underline"
          >
            clear filter
          </button>
        </div>
      )}

      {/* The grid itself. Plain table — encoder names are short enough that
       *  a CSS grid would be overkill, and a table preserves col/row alignment
       *  on long encoder names without ResizeObserver hacks. */}
      <div className="overflow-x-auto">
        <table className="border-collapse">
          <thead>
            <tr>
              <th className={headerCellCls} />
              {encoders.map((b) => (
                <th key={b} className={headerCellCls}>
                  {b}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {encoders.map((a, i) => (
              <tr key={a}>
                <th className={headerCellCls + " text-right"}>{a}</th>
                {encoders.map((b, j) => {
                  if (j < i) {
                    // Lower triangle — symmetric, leave blank.
                    return (
                      <td
                        key={b}
                        className={
                          baseCellCls + " bg-transparent border-transparent cursor-default"
                        }
                        aria-hidden="true"
                      />
                    );
                  }
                  if (i === j) {
                    const lone = stats.loneByEncoder[a] ?? 0;
                    const isActive = isFilterLone(activeFilter, a);
                    const tooltip = `${a} lone votes = ${lone} (mentions where only ${a} contributed)`;
                    return (
                      <Tooltip key={b}>
                        <TooltipTrigger asChild>
                          <td
                            data-testid={`encoder-covote-cell-${a}-${b}`}
                            className={`${baseCellCls} text-zinc-300 ${
                              isActive
                                ? "bg-cyan-500/30 ring-1 ring-cyan-400"
                                : "bg-white/[0.04] hover:bg-white/[0.08]"
                            }`}
                            onClick={() =>
                              onFilterChange(isActive ? null : { type: "lone", encoders: [a] })
                            }
                          >
                            [{lone}]
                          </td>
                        </TooltipTrigger>
                        <TooltipContent side="top" sideOffset={4} className={TOOLTIP_CLS}>
                          {tooltip}
                        </TooltipContent>
                      </Tooltip>
                    );
                  }
                  const p = stats.pairByKey[pairKey(a, b)];
                  if (!p) {
                    return (
                      <td key={b} className={baseCellCls + " bg-white/[0.02] text-zinc-600"}>
                        —
                      </td>
                    );
                  }
                  const isActive = isFilterPair(activeFilter, a, b);
                  const tooltip = `${a} ∩ ${b} = ${p.intersection} (${a} only=${p.onlyA}, ${b} only=${p.onlyB}) — ${p.jaccard.toFixed(2)} Jaccard`;
                  return (
                    <Tooltip key={b}>
                      <TooltipTrigger asChild>
                        <td
                          data-testid={`encoder-covote-cell-${a}-${b}`}
                          className={`${baseCellCls} ${jaccardBgClass(p.jaccard)} ${
                            isActive ? "ring-1 ring-cyan-300" : ""
                          }`}
                          onClick={() =>
                            onFilterChange(isActive ? null : { type: "pair", encoders: [a, b] })
                          }
                        >
                          {p.jaccard.toFixed(2)}
                        </td>
                      </TooltipTrigger>
                      <TooltipContent side="top" sideOffset={4} className={TOOLTIP_CLS}>
                        {tooltip}
                      </TooltipContent>
                    </Tooltip>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
