/**
 * TrendSparkline — 80×16 inline-SVG mini-chart for the cross-run trend
 * rail used by the State Inspector panel headers (Gap #8) and the
 * BenchmarkReport per-row leaderboard.
 *
 * Conventions:
 *  - ``points[i].value === null`` → gap. The polyline interrupts and
 *    resumes around null entries; the dot for that slot is not drawn
 *    so the user can see "this run lacked the metric" by absence.
 *  - The current run's dot is rendered larger + emerald-300; clicking
 *    any dot fires ``onSelectRun(point.runId)`` so the parent can swap
 *    the active run while preserving doc + node selection (StateInspector
 *    case) or update the report-source (BenchmarkReport case).
 *  - The trend arrow at the right edge compares the first non-null and
 *    last non-null values; direction is ``up`` / ``down`` / ``flat``.
 *    ``trend="up-good"`` colors emerald on up, red on down (and inverse
 *    for ``down-good`` — used for persist wall-clock where lower-is-
 *    better).
 *
 * No chart library — inline SVG only. Tooltip uses the catalyst-ui
 * Tooltip primitive with the same TOOLTIP_CLS as Gap #4.
 */

import { useMemo, useState } from "react";

import type { TrendMetric, TrendPoint } from "@/hooks/useTrendData";

const TOOLTIP_CLS =
  "z-50 max-w-sm rounded-md border border-white/10 bg-surface-1 text-zinc-100 px-3 py-2 shadow-xl text-[11px] leading-relaxed font-mono whitespace-pre-line";

const METRIC_LABELS: Record<TrendMetric, string> = {
  encoder_mention_count: "mentions",
  encoder_strict_f1: "strict F1",
  consensus_accepted_count: "accepted",
  consensus_strict_f1: "strict F1",
  pack_kept_pruned_ratio: "kept/pruned",
  spo_mean_props_per_window: "props/win",
  persist_wall_clock_seconds: "wall-clock (s)",
};

const METRIC_FMT: Partial<Record<TrendMetric, (v: number) => string>> = {
  encoder_strict_f1: (v) => v.toFixed(3),
  consensus_strict_f1: (v) => v.toFixed(3),
  pack_kept_pruned_ratio: (v) => v.toFixed(2),
  spo_mean_props_per_window: (v) => v.toFixed(2),
  persist_wall_clock_seconds: (v) => `${v.toFixed(1)}s`,
};

const fmtValue = (metric: TrendMetric, v: number): string => {
  const f = METRIC_FMT[metric];
  return f ? f(v) : String(v);
};

interface Props {
  points: TrendPoint[];
  metric: TrendMetric;
  /** The currently-active run id; the matching dot is highlighted and
   *  marked ``data-current="true"`` so e2e can locate it. */
  currentRunId: string | null;
  onSelectRun: (runId: string) => void;
  size?: { w: number; h: number };
  /** ``up-good`` (default) → upward trend = emerald, downward = red.
   *  ``down-good`` → inverse (used for persist wall-clock). */
  trend?: "up-good" | "down-good";
  /** Optional ARIA / hover label for the whole sparkline. Defaults to
   *  the metric name. */
  label?: string;
}

interface ScreenPoint extends TrendPoint {
  x: number;
  y: number;
  hasValue: boolean;
}

const PAD_X = 3;
const PAD_Y = 3;

function buildPolylineSegments(pts: ScreenPoint[]): string[] {
  // Walk left-to-right, splitting whenever a null breaks the line so the
  // SVG renders separate <polyline>s rather than a straight bridge across
  // the gap.
  const segments: string[] = [];
  let cur: string[] = [];
  for (const p of pts) {
    if (p.hasValue) {
      cur.push(`${p.x.toFixed(2)},${p.y.toFixed(2)}`);
    } else if (cur.length > 0) {
      segments.push(cur.join(" "));
      cur = [];
    }
  }
  if (cur.length > 0) segments.push(cur.join(" "));
  // Single-point segments don't render as a line; SVG still draws the
  // moveto so we keep them and let the dot stand alone.
  return segments.filter((s) => s.includes(" "));
}

export function TrendSparkline({
  points,
  metric,
  currentRunId,
  onSelectRun,
  size,
  trend = "up-good",
  label,
}: Props) {
  const w = size?.w ?? 80;
  const h = size?.h ?? 16;

  // Self-managed tooltip state. We don't use Radix Tooltip here for two
  // reasons:
  //   1. Radix `TooltipTrigger asChild` cloned over an SVG element was
  //      swallowing React `onClick` events under React 19, breaking
  //      click-to-jump (Gap #8 review §1).
  //   2. Radix's `TooltipContent` renders the visible portal WITHOUT
  //      `role="tooltip"` (only the sr-only VisuallyHidden annunciator
  //      gets that role), which made Playwright's
  //      ``locator('[role="tooltip"]').toBeVisible()`` resolve to the
  //      hidden sr-only copy and fail the spec.
  // Rolling our own tooltip as an absolutely-positioned <div> sibling of
  // the <svg> sidesteps both issues — the click handler lives on a plain
  // <rect> inside SVG (no Radix cloning), and the tooltip is a real HTML
  // div with ``role="tooltip"`` that Playwright sees as visible.
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const { screenPoints, direction, arrowColor, arrowGlyph } = useMemo(() => {
    const valued = points.filter((p): p is TrendPoint & { value: number } => p.value !== null);
    if (points.length === 0 || valued.length === 0) {
      return {
        screenPoints: [] as ScreenPoint[],
        direction: "flat" as const,
        arrowColor: "text-zinc-500",
        arrowGlyph: "→",
      };
    }
    const min = Math.min(...valued.map((p) => p.value));
    const max = Math.max(...valued.map((p) => p.value));
    const range = max - min;
    // Avoid a flat line stacking on the bottom edge — when range=0,
    // place every point at the vertical midline.
    const innerW = w - PAD_X * 2;
    const innerH = h - PAD_Y * 2;
    const screen: ScreenPoint[] = points.map((p, idx) => {
      const x = points.length === 1 ? w / 2 : PAD_X + (idx * innerW) / (points.length - 1);
      let y: number;
      if (p.value === null) {
        y = h / 2; // unused for null points; we don't draw the dot
      } else if (range === 0) {
        y = PAD_Y + innerH / 2;
      } else {
        // SVG y grows downward; higher value → smaller y.
        y = PAD_Y + innerH - ((p.value - min) / range) * innerH;
      }
      return { ...p, x, y, hasValue: p.value !== null };
    });
    // Direction: compare first vs last non-null.
    const first = valued[0]!.value;
    const last = valued[valued.length - 1]!.value;
    let dir: "up" | "down" | "flat" = "flat";
    if (last > first) dir = "up";
    else if (last < first) dir = "down";
    const goodDir = trend === "up-good" ? "up" : "down";
    let color = "text-zinc-500";
    if (dir !== "flat") {
      color = dir === goodDir ? "text-emerald-300" : "text-red-400";
    }
    const glyph = dir === "up" ? "↗" : dir === "down" ? "↘" : "→";
    return { screenPoints: screen, direction: dir, arrowColor: color, arrowGlyph: glyph };
  }, [points, w, h, trend]);

  const segments = useMemo(() => buildPolylineSegments(screenPoints), [screenPoints]);

  if (points.length === 0) {
    // Render a placeholder so the testid exists even before the index
    // loads — easier for e2e to wait on. Empty body, no dots.
    return (
      <span
        data-testid="trend-sparkline"
        data-empty="true"
        className="inline-flex items-center gap-1 align-middle"
        title={label ?? METRIC_LABELS[metric]}
      >
        <svg width={w} height={h} aria-hidden="true" />
      </span>
    );
  }

  const hovered =
    hoverIdx !== null && screenPoints[hoverIdx]?.hasValue ? screenPoints[hoverIdx] : null;
  const tooltipBody = hovered ? `${hovered.runId}\nvalue: ${fmtValue(metric, hovered.value!)}` : "";

  return (
    <span
      data-testid="trend-sparkline"
      data-metric={metric}
      className="inline-flex items-center gap-1 align-middle relative"
    >
      <svg
        width={w}
        height={h}
        viewBox={`0 0 ${w} ${h}`}
        className="overflow-visible"
        role="img"
        aria-label={label ?? `${METRIC_LABELS[metric]} trend (last ${points.length} runs)`}
      >
        {/* Polyline(s) — split around null gaps */}
        {segments.map((pointsStr, i) => (
          <polyline
            key={i}
            points={pointsStr}
            fill="none"
            stroke="currentColor"
            strokeWidth="1"
            className="text-zinc-500"
          />
        ))}
        {/* Per-point dots — null slots produce no <g>; the visible
         *  <circle> is decorative (pointer-events: none) and the 8x8
         *  transparent <rect> serves as the actual hit-test target so
         *  the dot is comfortably clickable even when only 2px tall.
         *
         *  Click + hover handlers live directly on the <rect>: no
         *  Radix `asChild` cloning, no `e.stopPropagation()`. The
         *  Gap #8 review (§1) traced the click-jump regression to a
         *  combination of Radix's pointerdown handling on a cloned
         *  SVG trigger AND the parent panel not actually wiring
         *  ``onJumpRun`` through to NerEncoderDetail/ConsensusDetail.
         *  Both are fixed by binding here and threading the prop in
         *  StateInspector's DetailRouter. */}
        {screenPoints.map((p, idx) => {
          if (!p.hasValue) return null;
          const isCurrent = currentRunId !== null && p.runId === currentRunId;
          const r = isCurrent ? 3 : 2;
          return (
            <g
              key={p.runId}
              data-testid={`trend-sparkline-point-${idx}`}
              data-run-id={p.runId}
              data-current={isCurrent ? "true" : "false"}
              data-value={p.value!}
              className="cursor-pointer"
            >
              <rect
                x={p.x - 4}
                y={p.y - 4}
                width={8}
                height={8}
                fill="transparent"
                pointerEvents="visiblePainted"
                onClick={() => onSelectRun(p.runId)}
                onMouseEnter={() => setHoverIdx(idx)}
                onMouseLeave={() => setHoverIdx((cur) => (cur === idx ? null : cur))}
                onFocus={() => setHoverIdx(idx)}
                onBlur={() => setHoverIdx((cur) => (cur === idx ? null : cur))}
              />
              <circle
                cx={p.x}
                cy={p.y}
                r={r}
                className={
                  isCurrent
                    ? "fill-emerald-300 stroke-emerald-300"
                    : "fill-zinc-400 stroke-zinc-400"
                }
                pointerEvents="none"
              />
            </g>
          );
        })}
      </svg>
      {/* Trend arrow — direction colored by metric polarity. */}
      <span
        data-testid="trend-sparkline-arrow"
        data-direction={direction}
        className={`text-[10px] font-mono leading-none ${arrowColor}`}
        aria-hidden="true"
      >
        {arrowGlyph}
      </span>
      {/* Self-managed tooltip — see hoverIdx state comment above for
       *  the rationale (Radix Tooltip didn't survive the SVG/asChild
       *  context and Playwright's role=tooltip query). The element
       *  carries `role="tooltip"` + a stable testid so the e2e spec's
       *  ``page.locator('[role="tooltip"]')`` finds the displayed
       *  copy, not the (sr-only) Radix annunciator. */}
      {hovered && (
        <span
          role="tooltip"
          data-testid="trend-sparkline-tooltip"
          data-state="open"
          className={`absolute left-0 -top-1 -translate-y-full pointer-events-none ${TOOLTIP_CLS}`}
        >
          {tooltipBody}
        </span>
      )}
    </span>
  );
}
