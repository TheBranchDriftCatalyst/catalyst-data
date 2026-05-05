/**
 * ConfidenceHistogram — inline SVG distribution of per-mention `confidence`
 * values for one NER encoder, with an optional GT-confirmed overlay drawn
 * over the background bars.
 *
 * Rendered between the type-tally pills and the mention list on
 * NerEncoderDetail. No chart-lib dep — bars are plain ``<rect>`` and the
 * threshold preview is a single ``<line>``.
 *
 * GT-list-empty vs GT-list-not-loaded:
 *  - ``gtList === null`` (or undefined)        → "no GT active": single-tone
 *    zinc bars, threshold preview shows ``keep N / total`` only (no P/R, we
 *    have no truth signal to score against).
 *  - ``gtList === []`` (GT active but unmatched for this doc) → same as
 *    "no GT" visually (foreground subset is empty so emerald never paints),
 *    BUT we keep the P/R math suppressed for the same reason. The single-tone
 *    fallback kicks in naturally because the GT-confirmed bars all have
 *    height 0.
 *  - ``gtList.length > 0`` → two-tone: zinc background + emerald foreground;
 *    threshold preview shows full ``P / R vs all-in P / R`` line.
 *
 * The "keep N / total" preview when no GT is active is intentional — we
 * don't fake P/R numbers when there's no truth to compare against.
 *
 * Bin width is 0.05 (20 bins covering [0, 1]). Confidences slightly outside
 * the [0, 1] range are clamped before binning so weirdly-calibrated
 * encoders don't drop rows from the histogram.
 */

import { useMemo, useState } from "react";

import type { GtMention } from "@/hooks/useRunReport";
import { matchesGtMention, type PredMention } from "@/lib/gt-match";

const BIN_COUNT = 20;
const BIN_WIDTH = 1 / BIN_COUNT; // 0.05
const SVG_W = 280;
const SVG_H = 64;
const CHART_PAD_L = 4;
const CHART_PAD_R = 4;
const CHART_PAD_T = 4;
const CHART_PAD_B = 12; // leaves room for axis labels
const CHART_W = SVG_W - CHART_PAD_L - CHART_PAD_R;
const CHART_H = SVG_H - CHART_PAD_T - CHART_PAD_B;
const BAR_W = CHART_W / BIN_COUNT;

interface MentionInput {
  confidence?: number | null;
  canonical_text?: string;
  text?: string;
  mention_type?: string;
  canonical_type?: string;
  type?: string;
  span_start?: number | null;
  span_end?: number | null;
  doc_id?: string;
  chunk_id?: string;
}

interface Props {
  mentions: MentionInput[];
  /** ``null`` = no active GT, ``[]`` = GT loaded but no rows scoped to
   *  this doc, ``[...]`` = match against this list. */
  gtList?: GtMention[] | null;
  encoderName: string;
}

interface BinRow {
  /** Bin index 0..BIN_COUNT-1. */
  idx: number;
  /** Inclusive lower bound, e.g. idx=13 → 0.65. */
  lower: number;
  /** Total mentions in this bin (regardless of GT). */
  total: number;
  /** Subset of ``total`` that matched a GT row. */
  gtConfirmed: number;
}

interface Computed {
  bins: BinRow[];
  total: number;
  gtTotal: number;
  /** Mentions dropped because they had no confidence value. */
  noConfCount: number;
  /** True iff every mention has null/undefined confidence. */
  allNullConfidence: boolean;
  /** True iff any single bin is more than 5× the median non-zero bin. */
  longTail: boolean;
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/** Map a confidence in [0, 1] to a bin index in [0, BIN_COUNT-1]. */
function binIndexFor(conf: number): number {
  const c = clamp(conf, 0, 1);
  // c === 1 collapses into the last bin rather than overflowing.
  if (c >= 1) return BIN_COUNT - 1;
  return Math.floor(c / BIN_WIDTH);
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    const a = sorted[mid - 1] ?? 0;
    const b = sorted[mid] ?? 0;
    return (a + b) / 2;
  }
  return sorted[mid] ?? 0;
}

function computeHistogram(
  mentions: MentionInput[],
  gtList: GtMention[] | null | undefined,
): Computed {
  const bins: BinRow[] = Array.from({ length: BIN_COUNT }, (_, idx) => ({
    idx,
    lower: idx * BIN_WIDTH,
    total: 0,
    gtConfirmed: 0,
  }));

  let total = 0;
  let gtTotal = 0;
  let noConfCount = 0;
  const hasGt = Array.isArray(gtList) && gtList.length > 0;

  for (const m of mentions) {
    const conf = m.confidence;
    if (conf == null || Number.isNaN(conf)) {
      noConfCount += 1;
      continue;
    }
    const idx = binIndexFor(conf);
    const bin = bins[idx];
    if (!bin) continue; // appease noUncheckedIndexedAccess
    bin.total += 1;
    total += 1;

    if (hasGt) {
      const pred: PredMention = {
        text: m.canonical_text ?? m.text ?? "",
        mention_type: m.canonical_type ?? m.mention_type ?? m.type,
        span_start: m.span_start ?? null,
        span_end: m.span_end ?? null,
        doc_id: m.doc_id,
        chunk_id: m.chunk_id,
      };
      if (matchesGtMention(pred, gtList!)) {
        bin.gtConfirmed += 1;
        gtTotal += 1;
      }
    }
  }

  // Long-tail signal: any single bin > 5× the median of non-empty bins.
  const nonZero = bins.map((b) => b.total).filter((v) => v > 0);
  const med = median(nonZero);
  const peak = nonZero.length > 0 ? Math.max(...nonZero) : 0;
  const longTail = med > 0 && peak > 5 * med;

  const allNullConfidence = mentions.length > 0 && total === 0;

  return { bins, total, gtTotal, noConfCount, allNullConfidence, longTail };
}

interface PreviewState {
  /** Hovered bin (or pinned via click). */
  binIdx: number;
}

export function ConfidenceHistogram({ mentions, gtList, encoderName }: Props) {
  const [hover, setHover] = useState<PreviewState | null>(null);

  const data = useMemo(() => computeHistogram(mentions, gtList), [mentions, gtList]);
  const hasActiveGt = Array.isArray(gtList) && gtList.length > 0;

  // Hovered bin → threshold preview.
  const previewIdx = hover?.binIdx ?? null;
  const previewLower = previewIdx != null ? (data.bins[previewIdx]?.lower ?? null) : null;

  // "keep N / total" at threshold = previewLower (≥ that confidence).
  // Computed unconditionally so the hook order matches across renders even
  // when the empty-state branch returns early.
  const previewKeep = useMemo(() => {
    if (previewIdx == null) return null;
    let keep = 0;
    let keepGt = 0;
    for (let i = previewIdx; i < BIN_COUNT; i += 1) {
      const b = data.bins[i];
      if (!b) continue;
      keep += b.total;
      keepGt += b.gtConfirmed;
    }
    return { keep, keepGt };
  }, [previewIdx, data.bins]);

  // Empty state — no scores at all.
  if (data.allNullConfidence) {
    return (
      <div
        data-testid="confidence-empty"
        className="text-zinc-500 text-[10px] italic"
        title={`${encoderName} did not emit per-mention confidence scores`}
      >
        no confidence scores reported by this encoder
      </div>
    );
  }

  // Y-axis scale — single source of truth for both bar tones. Round up to
  // a sane top so a single 1-mention bin doesn't fill the chart.
  const yMax = Math.max(1, ...data.bins.map((b) => b.total));

  const previewLine = (() => {
    if (previewIdx == null || previewLower == null || previewKeep == null) {
      // Default summary line — no hover.
      const range = "[0.00–1.00]";
      if (hasActiveGt) {
        return `range: ${range}  ·  ${data.total} mentions  ·  GT-confirmed: ${data.gtTotal}`;
      }
      return `range: ${range}  ·  ${data.total} mentions`;
    }
    const lowerStr = previewLower.toFixed(2);
    const total = data.total;
    if (hasActiveGt) {
      // Threshold P/R vs all-in P/R.
      // - kept set: predictions with conf >= threshold
      // - gtTotal is the # of GT-confirmed predictions across all bins;
      //   this is our proxy for the GT recall denominator (since we don't
      //   know GT rows that no encoder hit). It mirrors what the inspector
      //   already exposes elsewhere.
      const keep = previewKeep.keep;
      const keepGt = previewKeep.keepGt;
      const pAt = keep > 0 ? keepGt / keep : 0;
      const rAt = data.gtTotal > 0 ? keepGt / data.gtTotal : 0;
      const pAll = total > 0 ? data.gtTotal / total : 0;
      const rAll = 1;
      return (
        `at conf ≥ ${lowerStr}: keep ${keep} / ${total}, ${keepGt} GT-confirmed ` +
        `(P ${pAt.toFixed(2)}, R ${rAt.toFixed(2)} vs all-in P ${pAll.toFixed(2)}, R ${rAll.toFixed(2)})`
      );
    }
    // No GT — don't fake P/R numbers. Show keep + bin range only.
    const upperStr = (previewLower + BIN_WIDTH).toFixed(2);
    return (
      `at conf ≥ ${lowerStr}: keep ${previewKeep.keep} / ${total}  ·  ` +
      `bin [${lowerStr}–${upperStr})`
    );
  })();

  const bgFill = "rgb(113 113 122 / 0.6)"; // zinc-500/60 — single-tone mode
  const bgFillTwo = "rgb(113 113 122 / 0.4)"; // zinc-500/40 — background in two-tone
  const fgFill = "rgb(52 211 153 / 0.8)"; // emerald-400/80
  const thresholdStroke = "rgb(251 191 36)"; // amber-400

  return (
    <div data-testid="confidence-histogram" className="space-y-1">
      <div className="relative">
        <svg
          width={SVG_W}
          height={SVG_H}
          viewBox={`0 0 ${SVG_W} ${SVG_H}`}
          className="block"
          role="img"
          aria-label={`${encoderName} confidence histogram`}
          onMouseLeave={() => setHover(null)}
        >
          {/* Bars */}
          {data.bins.map((b) => {
            const x = CHART_PAD_L + b.idx * BAR_W;
            const hTotal = (b.total / yMax) * CHART_H;
            const yTotal = CHART_PAD_T + (CHART_H - hTotal);
            const bgColor = hasActiveGt ? bgFillTwo : bgFill;
            // Inner gap of 1px on each side keeps neighboring bars distinct.
            const innerW = Math.max(1, BAR_W - 1);
            return (
              <g key={b.idx}>
                {b.total > 0 && (
                  <rect x={x} y={yTotal} width={innerW} height={hTotal} fill={bgColor} />
                )}
                {hasActiveGt && b.gtConfirmed > 0 && (
                  <rect
                    x={x}
                    y={CHART_PAD_T + (CHART_H - (b.gtConfirmed / yMax) * CHART_H)}
                    width={innerW}
                    height={(b.gtConfirmed / yMax) * CHART_H}
                    fill={fgFill}
                  />
                )}
                {/* Hit-target rect — full chart-height so thin bars are still hoverable. */}
                <rect
                  data-testid={`confidence-bin-${b.idx}`}
                  x={x}
                  y={CHART_PAD_T}
                  width={innerW}
                  height={CHART_H}
                  fill="transparent"
                  onMouseEnter={() => setHover({ binIdx: b.idx })}
                  onClick={() => setHover({ binIdx: b.idx })}
                  style={{ cursor: "pointer" }}
                />
              </g>
            );
          })}

          {/* Threshold preview line — at the lower bound of the hovered bin. */}
          {previewIdx != null && previewLower != null && (
            <line
              x1={CHART_PAD_L + previewIdx * BAR_W}
              x2={CHART_PAD_L + previewIdx * BAR_W}
              y1={CHART_PAD_T}
              y2={CHART_PAD_T + CHART_H}
              stroke={thresholdStroke}
              strokeWidth={1}
              strokeDasharray="2 2"
              pointerEvents="none"
            />
          )}

          {/* Axis ticks at 0.0 / 0.5 / 1.0 */}
          <text
            x={CHART_PAD_L}
            y={SVG_H - 2}
            fontSize={8}
            fill="rgb(113 113 122)"
            fontFamily="ui-monospace, monospace"
          >
            0.0
          </text>
          <text
            x={CHART_PAD_L + CHART_W / 2}
            y={SVG_H - 2}
            fontSize={8}
            fill="rgb(113 113 122)"
            textAnchor="middle"
            fontFamily="ui-monospace, monospace"
          >
            0.5
          </text>
          <text
            x={SVG_W - CHART_PAD_R}
            y={SVG_H - 2}
            fontSize={8}
            fill="rgb(113 113 122)"
            textAnchor="end"
            fontFamily="ui-monospace, monospace"
          >
            1.0
          </text>
        </svg>

        {data.longTail && (
          <span
            className="absolute top-0 right-0 px-1 py-0.5 rounded bg-zinc-500/15 text-zinc-500 text-[8px] tracking-wide"
            title="One bin is >5× the median bin — distribution has a long tail"
          >
            long tail
          </span>
        )}
      </div>

      <div data-testid="confidence-preview" className="text-zinc-400 text-[10px] font-mono">
        {previewLine}
      </div>
    </div>
  );
}
