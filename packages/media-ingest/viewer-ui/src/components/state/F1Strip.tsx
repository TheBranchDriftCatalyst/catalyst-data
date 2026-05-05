/**
 * F1Strip — three-stat header row showing precision / recall / F1 for the
 * currently-selected encoder or consensus node, sourced from the per-run
 * benchmark report.
 *
 * Renders nothing when ``scores`` is null — i.e. no GT for this run, no
 * report.json yet, or this model wasn't scored. That matches the gap
 * doc's "no graceful-degrade noise" requirement.
 *
 * Optional ``comparison`` slot is consensus-only: a Δ pill (vs the best
 * single encoder) that answers "did consensus actually improve?".
 *
 * Color conventions:
 *  - F1 leading vs the comparison baseline → emerald
 *  - F1 lagging → amber (encoder) or red (consensus, when delta < 0)
 *  - Δ ~= 0 (within 0.005) → zinc
 */

import type { ReactNode } from "react";

export interface F1Scores {
  precision: number;
  recall: number;
  strict_f1: number;
  partial_f1?: number;
  /** Optional per-type strict-F1 breakdown for the tooltip. */
  per_type?: Record<string, number>;
}

export interface F1Comparison {
  /** consensus_f1 - best_encoder_f1. Positive = consensus wins. */
  delta: number;
  /** What the delta is computed against — e.g. "vs best encoder". */
  baselineLabel?: string;
}

interface Props {
  scores: F1Scores | null;
  comparison?: F1Comparison;
  /** Used by the encoder strip — colors the F1 number emerald when this
   *  encoder is at-or-above (best - 0.02), amber when lagging. */
  leadingThresholdMet?: boolean;
}

const fmt = (v: number) => v.toFixed(2);

function buildTooltip(scores: F1Scores): string {
  const lines: string[] = [];
  if (scores.partial_f1 !== undefined) {
    lines.push(`partial F1: ${fmt(scores.partial_f1)}`);
  }
  if (scores.per_type && Object.keys(scores.per_type).length > 0) {
    const sorted = Object.entries(scores.per_type).sort(([, a], [, b]) => b - a);
    lines.push("per-type strict F1:");
    for (const [type, f1] of sorted) {
      lines.push(`  ${type}: ${fmt(f1)}`);
    }
  }
  return lines.join("\n");
}

export function F1Strip({ scores, comparison, leadingThresholdMet }: Props): ReactNode {
  if (scores === null) return null;

  // F1 number color:
  //  - encoder mode (no comparison): emerald if leadingThresholdMet, amber otherwise
  //  - consensus mode (comparison set): always emerald (the Δ pill carries the
  //    win/loss signal so the F1 number itself stays neutral-positive).
  const f1Color =
    comparison !== undefined
      ? "text-emerald-300"
      : leadingThresholdMet === false
        ? "text-amber-300"
        : "text-emerald-300";

  // Δ pill — only when comparison is set.
  let deltaPill: ReactNode = null;
  if (comparison !== undefined) {
    const d = comparison.delta;
    const sign = d > 0 ? "+" : "";
    const label = `${sign}${fmt(d)}`;
    const pillClass =
      d > 0.005
        ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
        : d < -0.005
          ? "bg-red-500/15 text-red-300 border-red-500/30"
          : "bg-zinc-500/15 text-zinc-400 border-zinc-500/30";
    deltaPill = (
      <span
        data-testid="f1-strip-delta"
        className={`px-1.5 py-0.5 rounded border text-[9.5px] font-mono ${pillClass}`}
        title={comparison.baselineLabel ?? "vs best encoder"}
      >
        Δ {label}
      </span>
    );
  }

  return (
    <div
      data-testid="f1-strip"
      className="flex items-center gap-3 font-mono text-[11px]"
      title={buildTooltip(scores)}
    >
      <span>
        <span className="text-zinc-500">P </span>
        <span data-testid="f1-strip-precision" className="text-zinc-200">
          {fmt(scores.precision)}
        </span>
      </span>
      <span className="text-zinc-700">·</span>
      <span>
        <span className="text-zinc-500">R </span>
        <span data-testid="f1-strip-recall" className="text-zinc-200">
          {fmt(scores.recall)}
        </span>
      </span>
      <span className="text-zinc-700">·</span>
      <span>
        <span className="text-zinc-500">F1 </span>
        <span data-testid="f1-strip-f1" className={f1Color}>
          {fmt(scores.strict_f1)}
        </span>
      </span>
      {deltaPill}
    </div>
  );
}
