/**
 * PackThresholdHistograms — twin SVG histograms for pack-stage windows,
 * stacked above the existing kept/pruned tables on PackDetail.
 *
 * Visualises:
 *  - Chart A: distribution by `mention_count` (integer bins 0..max).
 *  - Chart B: distribution by `chars_per_mention` (log-ish bin edges).
 *
 * Bars are stacked vertically by lifecycle:
 *   bottom = kept (emerald), middle = pruned-too-few-mentions (amber),
 *   top    = pruned-sparse-density (red).
 *
 * Each chart carries a draggable amber threshold line that snaps to bin
 * edges and updates a live counterfactual readout below the chart. The
 * readouts intentionally do NOT cross-couple: the mention-count slider
 * never changes the "pruned-density" total (that bucket is gated by the
 * other axis), and vice-versa. This is called out in the readout text.
 *
 * Click-to-filter: clicking a bar bubbles a `(axis, binIdx)` pair up to
 * PackDetail, which dims non-matching rows in its kept + pruned tables.
 * Filter is single + mutually exclusive across axes — clicking a bar on
 * the other chart replaces (rather than ANDs) the filter. Click the same
 * bar again or the "clear filter" pill to reset.
 *
 * No new deps — pure SVG. Pointer-capture is used so drags don't break
 * when the cursor leaves the SVG.
 */

import { useEffect, useMemo, useRef, useState } from "react";

export interface PackWindowRecord {
  window_id: string;
  cluster_id?: string;
  mention_count: number;
  char_count: number;
  /** May be null for legacy `evidence_window_pruned` rows that pre-date
   *  the field — we treat null as "no signal" and exclude from chart B. */
  chars_per_mention: number | null;
  reason?: "too_few_mentions" | "sparse_density" | string;
}

export type PackHistAxis = "mention_count" | "chars_per_mention";

export interface PackHistFilter {
  axis: PackHistAxis;
  binIdx: number;
}

interface Props {
  kept: PackWindowRecord[];
  pruned: PackWindowRecord[];
  /** From `pack_evidence.details.prune_min_mentions`. */
  pruneMinMentions: number;
  /** From `pack_evidence.details.prune_max_chars_per_mention`. */
  pruneMaxCharsPerMention: number;
  activeFilter: PackHistFilter | null;
  onFilterChange: (next: PackHistFilter | null) => void;
}

// ---- Layout constants ---------------------------------------------------

const SVG_W = 460;
const SVG_H = 92;
const PAD_L = 24;
const PAD_R = 12;
const PAD_T = 6;
const PAD_B = 18; // axis label row
const CHART_W = SVG_W - PAD_L - PAD_R;
const CHART_H = SVG_H - PAD_T - PAD_B;
const HANDLE_W = 8;
const HANDLE_H = 12;

// ---- Colors -------------------------------------------------------------

const KEPT_FILL = "rgb(52 211 153 / 0.85)"; // emerald-400
const TOO_FEW_FILL = "rgb(251 191 36 / 0.85)"; // amber-400
const DENSITY_FILL = "rgb(248 113 113 / 0.85)"; // red-400
const THRESHOLD_STROKE = "rgb(251 191 36)"; // amber-400
const THRESHOLD_HANDLE_FILL = "rgb(251 191 36 / 0.85)";
const AXIS_TEXT = "rgb(113 113 122)"; // zinc-500

// ---- Bin edges for chars_per_mention ------------------------------------
//
// Spec gave [0, 50, 100, 200, 400, 800, 1600, ∞]. We keep these exactly
// because they're a roughly log-2 sweep that brackets the real prune
// threshold range we observe in the field (typical max_chars_per_mention
// is 800–1600). The final `Infinity` bin captures the long tail of
// extremely sparse windows so they're not silently dropped.
const CPM_BIN_EDGES: number[] = [0, 50, 100, 200, 400, 800, 1600, Infinity];

// Snap targets for the chars_per_mention drag handle. We expose every
// finite edge so the user can lock the slider to known reference points;
// the upper bound is replaced by the data's observed max at runtime.
const CPM_SNAP_TARGETS_FINITE = CPM_BIN_EDGES.filter((v) => Number.isFinite(v));

// ---- Helpers ------------------------------------------------------------

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function classifyReason(rec: PackWindowRecord): "kept" | "too_few" | "density" {
  if (!rec.reason) return "kept";
  if (rec.reason === "too_few_mentions") return "too_few";
  if (rec.reason === "sparse_density") return "density";
  // Unknown reason: count as "too_few" so we don't lose it visually. A
  // cleaner-future fix is a 4th bucket, but real corpora only emit the
  // two known reasons.
  return "too_few";
}

interface BinRow {
  idx: number;
  /** Lower bound (inclusive). */
  lower: number;
  /** Upper bound (exclusive); `Infinity` for the open-top last bin. */
  upper: number;
  kept: number;
  tooFew: number;
  density: number;
  total: number;
}

// Map a chars_per_mention value → bin index using CPM_BIN_EDGES. The
// upper edge is exclusive so 800 → bin "[800, 1600)".
function cpmBinIndex(value: number): number {
  for (let i = 0; i < CPM_BIN_EDGES.length - 1; i += 1) {
    const lower = CPM_BIN_EDGES[i] ?? 0;
    const upper = CPM_BIN_EDGES[i + 1] ?? Infinity;
    if (value >= lower && value < upper) return i;
  }
  return CPM_BIN_EDGES.length - 2; // last finite bin
}

interface MentionAggregate {
  bins: BinRow[];
  maxCount: number;
  yMax: number;
}

function aggregateMentionCount(
  kept: PackWindowRecord[],
  pruned: PackWindowRecord[],
): MentionAggregate {
  const all = [...kept, ...pruned];
  if (all.length === 0) {
    return { bins: [], maxCount: 0, yMax: 0 };
  }
  const maxCount = Math.max(...all.map((w) => w.mention_count ?? 0), 0);
  const bins: BinRow[] = Array.from({ length: maxCount + 1 }, (_, idx) => ({
    idx,
    lower: idx,
    upper: idx + 1,
    kept: 0,
    tooFew: 0,
    density: 0,
    total: 0,
  }));
  for (const w of all) {
    const idx = clamp(w.mention_count ?? 0, 0, maxCount);
    const bin = bins[idx];
    if (!bin) continue;
    const klass = classifyReason(w);
    if (klass === "kept") bin.kept += 1;
    else if (klass === "too_few") bin.tooFew += 1;
    else bin.density += 1;
    bin.total += 1;
  }
  const yMax = Math.max(1, ...bins.map((b) => b.total));
  return { bins, maxCount, yMax };
}

interface CpmAggregate extends MentionAggregate {
  finiteUpper: number;
}

function aggregateCharsPerMention(
  kept: PackWindowRecord[],
  pruned: PackWindowRecord[],
): CpmAggregate {
  const bins: BinRow[] = CPM_BIN_EDGES.slice(0, -1).map((lower, idx) => ({
    idx,
    lower,
    upper: CPM_BIN_EDGES[idx + 1] ?? Infinity,
    kept: 0,
    tooFew: 0,
    density: 0,
    total: 0,
  }));

  let observedMax = 0;
  let sawAny = false;

  const consider = (w: PackWindowRecord) => {
    const cpm = w.chars_per_mention;
    if (cpm == null || Number.isNaN(cpm)) return;
    sawAny = true;
    if (cpm > observedMax && Number.isFinite(cpm)) observedMax = cpm;
    const idx = cpmBinIndex(cpm);
    const bin = bins[idx];
    if (!bin) return;
    const klass = classifyReason(w);
    if (klass === "kept") bin.kept += 1;
    else if (klass === "too_few") bin.tooFew += 1;
    else bin.density += 1;
    bin.total += 1;
  };
  for (const w of kept) consider(w);
  for (const w of pruned) consider(w);

  // For the open-top last bin (∞), substitute the observed max so the log
  // mapping has a finite right edge. If we have NO data, fall back to the
  // last finite edge (1600) so the chart still draws sensibly.
  const lastFiniteEdge = CPM_BIN_EDGES[CPM_BIN_EDGES.length - 2] ?? 1600;
  const finiteUpper = sawAny ? Math.max(observedMax, lastFiniteEdge * 1.1) : lastFiniteEdge;

  const yMax = Math.max(1, ...bins.map((b) => b.total));
  return {
    bins,
    maxCount: 0,
    yMax,
    finiteUpper,
  };
}

// ---- log-space mapper for chars_per_mention X axis ----------------------

function logScaleX(value: number, finiteUpper: number): number {
  // Map [0, finiteUpper] to [PAD_L, PAD_L + CHART_W] in log space. We add
  // 1 to avoid log(0).
  const min = Math.log10(1);
  const max = Math.log10(finiteUpper + 1);
  if (max <= min) return PAD_L;
  const v = clamp(value, 0, finiteUpper);
  const t = (Math.log10(v + 1) - min) / (max - min);
  return PAD_L + t * CHART_W;
}

function inverseLogScaleX(px: number, finiteUpper: number): number {
  const min = Math.log10(1);
  const max = Math.log10(finiteUpper + 1);
  const t = clamp((px - PAD_L) / CHART_W, 0, 1);
  const log = min + t * (max - min);
  return Math.pow(10, log) - 1;
}

// ---- Stacked bar renderer -----------------------------------------------

interface BarLayout {
  x: number;
  width: number;
}

/**
 * Render a stacked bar at (x, width) for one BinRow. Bottom=kept,
 * middle=tooFew, top=density. Uses yMax for vertical scaling.
 */
function StackedBar({
  bin,
  layout,
  yMax,
  axis,
  onClick,
  isFiltered,
  testId,
}: {
  bin: BinRow;
  layout: BarLayout;
  yMax: number;
  axis: PackHistAxis;
  onClick: () => void;
  isFiltered: boolean;
  testId: string;
}) {
  const { x, width } = layout;
  const innerW = Math.max(1, width - 1);
  const totalH = (bin.total / yMax) * CHART_H;
  const stackTopY = PAD_T + (CHART_H - totalH);
  const segs: { fill: string; h: number }[] = [
    { fill: KEPT_FILL, h: (bin.kept / yMax) * CHART_H },
    { fill: TOO_FEW_FILL, h: (bin.tooFew / yMax) * CHART_H },
    { fill: DENSITY_FILL, h: (bin.density / yMax) * CHART_H },
  ];
  // We render bottom-up: kept at the bottom of the stack.
  let cursor = PAD_T + CHART_H;
  return (
    <g
      data-testid={testId}
      data-axis={axis}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      style={{ cursor: "pointer", opacity: isFiltered ? 1 : undefined }}
    >
      {/* Hit-target: full chart-height so even zero-bins are clickable
          (lets the user clear filter by re-clicking). */}
      <rect x={x} y={PAD_T} width={innerW} height={CHART_H} fill="transparent" />
      {segs.map((seg, i) => {
        if (seg.h <= 0) return null;
        const segY = cursor - seg.h;
        cursor -= seg.h;
        return (
          <rect
            key={i}
            x={x}
            y={segY}
            width={innerW}
            height={seg.h}
            fill={seg.fill}
            pointerEvents="none"
          />
        );
      })}
      {/* Outline when this bin is the active filter target. */}
      {isFiltered && bin.total > 0 && (
        <rect
          x={x - 0.5}
          y={stackTopY - 0.5}
          width={innerW + 1}
          height={totalH + 1}
          fill="none"
          stroke="rgb(34 211 238)"
          strokeWidth={1}
          pointerEvents="none"
        />
      )}
    </g>
  );
}

// ---- Threshold-handle drag wiring ---------------------------------------

interface DragState {
  pointerId: number;
  startClientX: number;
  startValue: number;
}

interface ThresholdLineProps {
  xPx: number;
  testId: string;
  onPointerDown: (ev: React.PointerEvent<SVGRectElement>) => void;
  /** When true, render a transient `animate-pulse` overlay on the handle.
   *  Gap #7: fired by URL-seeded mounts coming from pruned_window detail. */
  pulse?: boolean;
  /** Stable suffix for the pulse element's testid (e.g. "mention-count"). */
  pulseTestIdSuffix?: string;
}

function ThresholdLine({
  xPx,
  testId,
  onPointerDown,
  pulse,
  pulseTestIdSuffix,
}: ThresholdLineProps) {
  const handleX = xPx - HANDLE_W / 2;
  const handleY = PAD_T + CHART_H / 2 - HANDLE_H / 2;
  return (
    <g pointerEvents="auto">
      <line
        x1={xPx}
        x2={xPx}
        y1={PAD_T}
        y2={PAD_T + CHART_H}
        stroke={THRESHOLD_STROKE}
        strokeWidth={1}
        strokeDasharray="3 2"
        pointerEvents="none"
      />
      <rect
        data-testid={testId}
        x={handleX}
        y={handleY}
        width={HANDLE_W}
        height={HANDLE_H}
        rx={1.5}
        fill={THRESHOLD_HANDLE_FILL}
        stroke="rgb(217 119 6)"
        strokeWidth={0.75}
        style={{ cursor: "ew-resize" }}
        onPointerDown={onPointerDown}
      />
      {pulse && pulseTestIdSuffix && (
        // Larger halo with `animate-pulse` to draw attention. Pointer events
        // disabled so the underlying handle stays draggable. SVG-friendly
        // pulse (Tailwind's `animate-pulse` is opacity-only — works on SVG).
        <rect
          data-testid={`pack-threshold-handle-pulse-${pulseTestIdSuffix}`}
          className="animate-pulse"
          x={handleX - 4}
          y={handleY - 4}
          width={HANDLE_W + 8}
          height={HANDLE_H + 8}
          rx={3}
          fill="none"
          stroke="rgb(34 211 238)"
          strokeWidth={1.5}
          pointerEvents="none"
        />
      )}
    </g>
  );
}

// ---- Component ----------------------------------------------------------

export function PackThresholdHistograms({
  kept,
  pruned,
  pruneMinMentions,
  pruneMaxCharsPerMention,
  activeFilter,
  onFilterChange,
}: Props) {
  const mentionAgg = useMemo(() => aggregateMentionCount(kept, pruned), [kept, pruned]);
  const cpmAgg = useMemo(() => aggregateCharsPerMention(kept, pruned), [kept, pruned]);

  // Counterfactual previews start in-sync with the configured thresholds;
  // dragging the handle moves only the local preview, never the prop.
  // Gap #7 cross-panel handoff: when the URL carries `packPreviewMin` /
  // `packPreviewMaxCpm` (set by the pruned_window "tune in pack" link),
  // seed the previews with those values, render a transient pulse on
  // the affected handle for ~1.5s, then strip the params from the URL so
  // a refresh doesn't keep re-pulsing.
  // Read URL-seeded values once on mount. `useMemo` with empty deps means
  // we capture the URL state at first render — subsequent re-renders read
  // from the cached value, so the strip-on-mount effect below doesn't
  // race with the seed read. (In React 19 dev-mode StrictMode the
  // component remounts once during local dev — the e2e suite runs against
  // the production build where this isn't a factor.)
  const seeded = useMemo(() => {
    if (typeof window === "undefined")
      return { min: null as number | null, cpm: null as number | null };
    const p = new URLSearchParams(window.location.search);
    const minRaw = p.get("packPreviewMin");
    const cpmRaw = p.get("packPreviewMaxCpm");
    const minNum = minRaw != null ? Number(minRaw) : NaN;
    const cpmNum = cpmRaw != null ? Number(cpmRaw) : NaN;
    return {
      min: Number.isFinite(minNum) ? minNum : null,
      cpm: Number.isFinite(cpmNum) ? cpmNum : null,
    };
  }, []);

  const [previewMin, setPreviewMin] = useState<number>(
    seeded.min != null ? clamp(Math.round(seeded.min), 0, 1_000_000) : pruneMinMentions,
  );
  const [previewMaxCpm, setPreviewMaxCpm] = useState<number>(
    seeded.cpm != null ? Math.max(0, seeded.cpm) : pruneMaxCharsPerMention,
  );

  // Pulse flags: true for ~1.5s after a URL-seeded mount, then off.
  const [pulseMin, setPulseMin] = useState<boolean>(seeded.min != null);
  const [pulseCpm, setPulseCpm] = useState<boolean>(seeded.cpm != null);

  useEffect(() => {
    if (seeded.min == null && seeded.cpm == null) return;
    // Strip the seed params from the URL so reload doesn't re-trigger.
    if (typeof window !== "undefined") {
      const p = new URLSearchParams(window.location.search);
      if (p.has("packPreviewMin") || p.has("packPreviewMaxCpm")) {
        p.delete("packPreviewMin");
        p.delete("packPreviewMaxCpm");
        const qs = p.toString();
        const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
        window.history.replaceState({}, "", url);
      }
    }
    const t = window.setTimeout(() => {
      setPulseMin(false);
      setPulseCpm(false);
    }, 1500);
    return () => window.clearTimeout(t);
  }, [seeded.min, seeded.cpm]);

  const mentionDrag = useRef<DragState | null>(null);
  const cpmDrag = useRef<DragState | null>(null);

  // ---- Chart A: mention_count -------------------------------------------

  const mentionMax = mentionAgg.maxCount;
  const mentionBarWidth = mentionMax > 0 ? CHART_W / (mentionMax + 1) : CHART_W;

  const mentionBaseCounts = useMemo(() => {
    let k = 0;
    let a = 0;
    let d = 0;
    k = kept.length;
    for (const w of pruned) {
      const klass = classifyReason(w);
      if (klass === "too_few") a += 1;
      else if (klass === "density") d += 1;
      else a += 1;
    }
    return { k, a, d };
  }, [kept, pruned]);

  // Counterfactual: at min_mentions ≥ M, how many windows would survive
  // the mention-count gate? Only kept + too_few rows are eligible to flip
  // (density-pruned rows are pruned by the OTHER axis — they stay pruned).
  const mentionPreview = useMemo(() => {
    let keepNew = 0;
    let dropTooFew = 0;
    for (const w of [...kept, ...pruned]) {
      const klass = classifyReason(w);
      if (klass === "density") continue;
      if ((w.mention_count ?? 0) >= previewMin) keepNew += 1;
      else dropTooFew += 1;
    }
    return { keepNew, dropTooFew };
  }, [kept, pruned, previewMin]);

  const onMentionHandleDown = (ev: React.PointerEvent<SVGRectElement>) => {
    ev.preventDefault();
    ev.stopPropagation();
    (ev.currentTarget as Element).setPointerCapture?.(ev.pointerId);
    mentionDrag.current = {
      pointerId: ev.pointerId,
      startClientX: ev.clientX,
      startValue: previewMin,
    };
  };

  const onMentionHandleMove = (ev: React.PointerEvent<Element>) => {
    const drag = mentionDrag.current;
    if (!drag || drag.pointerId !== ev.pointerId) return;
    if (mentionBarWidth <= 0) return;
    // Convert client-X delta → bin delta.
    const dxPx = ev.clientX - drag.startClientX;
    const dxBins = dxPx / mentionBarWidth;
    const next = Math.round(drag.startValue + dxBins);
    // Clamp: allow [0, maxCount + 1]. Above maxCount means "would prune
    // everything" — useful as an extreme readout.
    setPreviewMin(clamp(next, 0, mentionMax + 1));
  };

  const onMentionHandleUp = (ev: React.PointerEvent<Element>) => {
    const drag = mentionDrag.current;
    if (!drag || drag.pointerId !== ev.pointerId) return;
    (ev.currentTarget as Element).releasePointerCapture?.(ev.pointerId);
    mentionDrag.current = null;
  };

  // ---- Chart B: chars_per_mention ---------------------------------------

  const cpmFiniteUpper = cpmAgg.finiteUpper;

  const cpmPreview = useMemo(() => {
    let keepNew = 0;
    let newDensity = 0;
    for (const w of [...kept, ...pruned]) {
      const klass = classifyReason(w);
      if (klass === "too_few") continue; // gated by other axis
      const cpm = w.chars_per_mention;
      if (cpm == null) {
        // Legacy row with no signal — be conservative: keep.
        keepNew += 1;
        continue;
      }
      if (cpm <= previewMaxCpm) keepNew += 1;
      else newDensity += 1;
    }
    return { keepNew, newDensity };
  }, [kept, pruned, previewMaxCpm]);

  const onCpmHandleDown = (ev: React.PointerEvent<SVGRectElement>) => {
    ev.preventDefault();
    ev.stopPropagation();
    (ev.currentTarget as Element).setPointerCapture?.(ev.pointerId);
    cpmDrag.current = {
      pointerId: ev.pointerId,
      startClientX: ev.clientX,
      startValue: previewMaxCpm,
    };
  };

  const onCpmHandleMove = (ev: React.PointerEvent<Element>) => {
    const drag = cpmDrag.current;
    if (!drag || drag.pointerId !== ev.pointerId) return;
    // Convert client-X delta to a value in log-space. We compute the px
    // position the handle should move to and invert.
    const startPx = logScaleX(drag.startValue, cpmFiniteUpper);
    const newPx = startPx + (ev.clientX - drag.startClientX);
    const raw = inverseLogScaleX(newPx, cpmFiniteUpper);
    // Snap to the nearest known bin edge OR the observed max.
    const targets = [...CPM_SNAP_TARGETS_FINITE, cpmFiniteUpper];
    let snapped = targets[0] ?? 0;
    let bestDist = Infinity;
    for (const t of targets) {
      const d = Math.abs(t - raw);
      if (d < bestDist) {
        bestDist = d;
        snapped = t;
      }
    }
    setPreviewMaxCpm(clamp(snapped, 0, cpmFiniteUpper));
  };

  const onCpmHandleUp = (ev: React.PointerEvent<Element>) => {
    const drag = cpmDrag.current;
    if (!drag || drag.pointerId !== ev.pointerId) return;
    (ev.currentTarget as Element).releasePointerCapture?.(ev.pointerId);
    cpmDrag.current = null;
  };

  // ---- Empty state ------------------------------------------------------

  const noWindows = kept.length === 0 && pruned.length === 0;

  // ---- Filter helpers ---------------------------------------------------

  const isBinFiltered = (axis: PackHistAxis, idx: number): boolean =>
    activeFilter != null && activeFilter.axis === axis && activeFilter.binIdx === idx;

  const onBarClick = (axis: PackHistAxis, idx: number) => {
    if (isBinFiltered(axis, idx)) onFilterChange(null);
    else onFilterChange({ axis, binIdx: idx });
  };

  // ---- Readout strings --------------------------------------------------

  const mentionReadout = (() => {
    const { k, a, d } = mentionBaseCounts;
    if (previewMin === pruneMinMentions) {
      return `current min_mentions = ${pruneMinMentions} · ${k} kept · ${a} pruned-too-few · ${d} pruned-density`;
    }
    return (
      `at min_mentions ≥ ${previewMin}: keep ${mentionPreview.keepNew} (was ${k}), ` +
      `lose ${mentionPreview.dropTooFew} to too_few (was ${a}), ` +
      `${d} still pruned-density (unchanged — gated by chars/mention)`
    );
  })();

  const cpmReadout = (() => {
    const { k, a, d } = mentionBaseCounts;
    if (previewMaxCpm === pruneMaxCharsPerMention) {
      return `current max_chars_per_mention = ${pruneMaxCharsPerMention} · ${k} kept · ${a} pruned-too-few · ${d} pruned-density`;
    }
    const cpmStr = Number.isFinite(previewMaxCpm) ? previewMaxCpm.toFixed(0) : "∞";
    return (
      `at max_chars_per_mention ≤ ${cpmStr}: keep ${cpmPreview.keepNew} (was ${k}), ` +
      `${cpmPreview.newDensity} newly-pruned-density (was ${d}), ` +
      `${a} still pruned-too-few (unchanged — gated by min_mentions)`
    );
  })();

  // ---- Render -----------------------------------------------------------

  if (noWindows) {
    return (
      <div
        data-testid="pack-threshold-histograms"
        className="rounded border border-zinc-500/20 px-3 py-2 text-[10px] font-mono text-zinc-500 italic"
      >
        no windows yet — pack stage emitted neither kept nor pruned rows
      </div>
    );
  }

  // X-axis label formatting helpers
  const cpmTickValues = CPM_BIN_EDGES.filter((v) => Number.isFinite(v));
  const mentionTickStep = Math.max(1, Math.ceil((mentionMax + 1) / 8));

  // Threshold X-positions
  const mentionThresholdX = PAD_L + clamp(previewMin, 0, mentionMax + 1) * mentionBarWidth;
  const cpmThresholdX = logScaleX(previewMaxCpm, cpmFiniteUpper);

  return (
    <div
      data-testid="pack-threshold-histograms"
      className="space-y-3 rounded border border-zinc-500/15 bg-zinc-900/30 p-3"
    >
      {/* Header strip with filter-clear pill + legend */}
      <div className="flex items-center justify-between text-[9px] uppercase tracking-wide">
        <span className="text-zinc-500">threshold counterfactuals</span>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1 text-zinc-400">
            <span className="inline-block w-2 h-2 rounded-sm" style={{ background: KEPT_FILL }} />
            kept
          </span>
          <span className="flex items-center gap-1 text-zinc-400">
            <span
              className="inline-block w-2 h-2 rounded-sm"
              style={{ background: TOO_FEW_FILL }}
            />
            too few
          </span>
          <span className="flex items-center gap-1 text-zinc-400">
            <span
              className="inline-block w-2 h-2 rounded-sm"
              style={{ background: DENSITY_FILL }}
            />
            sparse
          </span>
          {activeFilter && (
            <button
              type="button"
              data-testid="pack-filter-clear"
              onClick={() => onFilterChange(null)}
              className="px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 hover:bg-cyan-500/25 normal-case tracking-normal"
            >
              clear filter
            </button>
          )}
        </div>
      </div>

      {/* Chart A — mention_count */}
      <div className="space-y-1">
        <div className="text-[9px] uppercase tracking-wide text-zinc-500">
          mention_count distribution
        </div>
        <svg
          data-testid="pack-histogram-mention-count"
          width={SVG_W}
          height={SVG_H}
          viewBox={`0 0 ${SVG_W} ${SVG_H}`}
          className="block max-w-full"
          role="img"
          aria-label="pack mention_count histogram"
          onPointerMove={onMentionHandleMove}
          onPointerUp={onMentionHandleUp}
          onPointerCancel={onMentionHandleUp}
        >
          {/* Y-axis baseline */}
          <line
            x1={PAD_L}
            x2={PAD_L + CHART_W}
            y1={PAD_T + CHART_H}
            y2={PAD_T + CHART_H}
            stroke={AXIS_TEXT}
            strokeWidth={0.5}
          />
          {/* Bars */}
          {mentionAgg.bins.map((b) => (
            <StackedBar
              key={b.idx}
              bin={b}
              layout={{ x: PAD_L + b.idx * mentionBarWidth, width: mentionBarWidth }}
              yMax={mentionAgg.yMax}
              axis="mention_count"
              onClick={() => onBarClick("mention_count", b.idx)}
              isFiltered={isBinFiltered("mention_count", b.idx)}
              testId={`pack-bin-mention-count-${b.idx}`}
            />
          ))}
          {/* X-axis ticks */}
          {mentionAgg.bins
            .filter((b) => b.idx % mentionTickStep === 0 || b.idx === mentionMax)
            .map((b) => (
              <text
                key={`tick-${b.idx}`}
                x={PAD_L + b.idx * mentionBarWidth + mentionBarWidth / 2}
                y={SVG_H - 4}
                fontSize={8}
                fill={AXIS_TEXT}
                textAnchor="middle"
                fontFamily="ui-monospace, monospace"
              >
                {b.idx}
              </text>
            ))}
          {/* Y-axis label (yMax) */}
          <text
            x={PAD_L - 3}
            y={PAD_T + 6}
            fontSize={8}
            fill={AXIS_TEXT}
            textAnchor="end"
            fontFamily="ui-monospace, monospace"
          >
            {mentionAgg.yMax}
          </text>
          {/* Threshold line + handle */}
          <ThresholdLine
            xPx={mentionThresholdX}
            testId="pack-threshold-handle-mention-count"
            onPointerDown={onMentionHandleDown}
            pulse={pulseMin}
            pulseTestIdSuffix="mention-count"
          />
        </svg>
        <div
          data-testid="pack-readout-mention-count"
          className="text-[10px] font-mono text-zinc-400"
        >
          {mentionReadout}
        </div>
      </div>

      {/* Chart B — chars_per_mention */}
      <div className="space-y-1">
        <div className="text-[9px] uppercase tracking-wide text-zinc-500">
          chars_per_mention distribution (log-scaled)
        </div>
        <svg
          data-testid="pack-histogram-chars-per-mention"
          width={SVG_W}
          height={SVG_H}
          viewBox={`0 0 ${SVG_W} ${SVG_H}`}
          className="block max-w-full"
          role="img"
          aria-label="pack chars_per_mention histogram"
          onPointerMove={onCpmHandleMove}
          onPointerUp={onCpmHandleUp}
          onPointerCancel={onCpmHandleUp}
        >
          {/* Y-axis baseline */}
          <line
            x1={PAD_L}
            x2={PAD_L + CHART_W}
            y1={PAD_T + CHART_H}
            y2={PAD_T + CHART_H}
            stroke={AXIS_TEXT}
            strokeWidth={0.5}
          />
          {/* Bars — width spans [logX(lower), logX(upper)] */}
          {cpmAgg.bins.map((b) => {
            const lowerPx = logScaleX(b.lower, cpmFiniteUpper);
            const upperPx = Number.isFinite(b.upper)
              ? logScaleX(b.upper, cpmFiniteUpper)
              : PAD_L + CHART_W;
            const width = Math.max(1, upperPx - lowerPx);
            return (
              <StackedBar
                key={b.idx}
                bin={b}
                layout={{ x: lowerPx, width }}
                yMax={cpmAgg.yMax}
                axis="chars_per_mention"
                onClick={() => onBarClick("chars_per_mention", b.idx)}
                isFiltered={isBinFiltered("chars_per_mention", b.idx)}
                testId={`pack-bin-chars-per-mention-${b.idx}`}
              />
            );
          })}
          {/* X-axis ticks at the canonical edge values */}
          {cpmTickValues.map((v) => (
            <text
              key={`cpm-tick-${v}`}
              x={logScaleX(v, cpmFiniteUpper)}
              y={SVG_H - 4}
              fontSize={8}
              fill={AXIS_TEXT}
              textAnchor="middle"
              fontFamily="ui-monospace, monospace"
            >
              {v}
            </text>
          ))}
          {/* Y-axis label */}
          <text
            x={PAD_L - 3}
            y={PAD_T + 6}
            fontSize={8}
            fill={AXIS_TEXT}
            textAnchor="end"
            fontFamily="ui-monospace, monospace"
          >
            {cpmAgg.yMax}
          </text>
          {/* Threshold line + handle */}
          <ThresholdLine
            xPx={cpmThresholdX}
            testId="pack-threshold-handle-chars-per-mention"
            onPointerDown={onCpmHandleDown}
            pulse={pulseCpm}
            pulseTestIdSuffix="chars-per-mention"
          />
        </svg>
        <div
          data-testid="pack-readout-chars-per-mention"
          className="text-[10px] font-mono text-zinc-400"
        >
          {cpmReadout}
        </div>
      </div>
    </div>
  );
}
