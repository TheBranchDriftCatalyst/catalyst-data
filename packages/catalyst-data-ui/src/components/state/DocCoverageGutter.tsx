/**
 * DocCoverageGutter — right-edge minimap of consensus mention density and
 * GT recall holes for the DocumentSourcePanel (Gap #6 from the
 * data-scientist tour).
 *
 * Renders a 12px-wide vertical SVG strip. The doc is discretised into
 * ``binCount`` (default 150) equal-width bins; for each bin we count how
 * many accepted-consensus mention spans intersect the bin's char range,
 * and draw a cyan rectangle whose opacity is proportional to that count
 * (normalised by the global per-doc max). When GT is loaded for this doc
 * (i.e. ``gtSpans`` has ≥ 1 entry), an inner amber stripe is drawn for
 * any bin where GT has at least one mention but consensus has zero —
 * making "we missed everything in this region" pop visually. A saturated
 * cyan rectangle marks the bin range corresponding to the currently
 * selected ``spo_window``'s char range.
 *
 * Click-to-scroll: clicking anywhere on the gutter scrolls the
 * ``scrollRef`` element so that the bin's leading char position lands at
 * the top of the scroller. Reuses the same scrollTo pattern as the
 * existing left-side chunk minimap (see DocumentSourcePanel.onMinimapClick).
 *
 * Empty-state behaviour:
 *   - No consensus mentions at all → render the gutter as a pale zinc
 *     track. Don't hide it — its presence cues "zero coverage on this
 *     doc".
 *   - ``gtSpans`` is null OR an empty list → omit the amber recall-hole
 *     track entirely. (Mirrors Gap #3 — null = no GT, [] = GT loaded but
 *     populated with zero mentions; in either case there is nothing
 *     meaningful to render on the recall track.)
 */

import { useMemo, useRef, useState, useEffect } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@thebranchdriftcatalyst/catalyst-ui";

const TOOLTIP_CLS =
  "z-50 max-w-sm rounded-md border border-white/10 bg-surface-1 text-zinc-100 px-3 py-2 shadow-xl text-[11px] leading-relaxed font-mono whitespace-pre-line";

interface Span {
  start: number;
  end: number;
}

interface Props {
  totalChars: number;
  consensusSpans: Span[];
  /** ``null`` → no GT loaded for this run. ``[]`` → GT loaded but empty
   *  (e.g. active-GT has 0 mentions populated). In both cases we omit
   *  the recall-hole track. Only render it when len > 0. */
  gtSpans?: Span[] | null;
  selectedWindow?: { start: number; end: number } | null;
  scrollRef: React.RefObject<HTMLElement | null>;
  binCount?: number;
}

interface Bin {
  /** char-range covered by this bin (start inclusive, end exclusive). */
  charStart: number;
  charEnd: number;
  consensusCount: number;
  gtCount: number;
}

/** Returns ``true`` when ``[a, b)`` intersects ``[c, d)`` over chars.
 *  Strict half-open intervals so adjacent bins don't double-count a span
 *  whose end falls exactly on a bin boundary. */
function _intersects(a: number, b: number, c: number, d: number): boolean {
  return a < d && c < b;
}

function _computeBins(
  totalChars: number,
  consensusSpans: Span[],
  gtSpans: Span[] | null | undefined,
  binCount: number,
): { bins: Bin[]; maxConsensus: number } {
  const bins: Bin[] = new Array(binCount);
  const safeTotal = Math.max(1, totalChars);
  const stride = safeTotal / binCount;
  for (let i = 0; i < binCount; i++) {
    const charStart = Math.floor(i * stride);
    const charEnd = i === binCount - 1 ? safeTotal : Math.floor((i + 1) * stride);
    bins[i] = { charStart, charEnd, consensusCount: 0, gtCount: 0 };
  }
  for (const s of consensusSpans) {
    if (s.start == null || s.end == null) continue;
    const a = Math.max(0, s.start);
    const b = Math.min(safeTotal, s.end);
    if (b <= a) continue;
    const firstBin = Math.max(0, Math.floor(a / stride));
    const lastBin = Math.min(binCount - 1, Math.floor((b - 1) / stride));
    for (let i = firstBin; i <= lastBin; i++) {
      const bin = bins[i]!;
      if (_intersects(a, b, bin.charStart, bin.charEnd)) bin.consensusCount += 1;
    }
  }
  if (gtSpans && gtSpans.length > 0) {
    for (const s of gtSpans) {
      if (s.start == null || s.end == null) continue;
      const a = Math.max(0, s.start);
      const b = Math.min(safeTotal, s.end);
      if (b <= a) continue;
      const firstBin = Math.max(0, Math.floor(a / stride));
      const lastBin = Math.min(binCount - 1, Math.floor((b - 1) / stride));
      for (let i = firstBin; i <= lastBin; i++) {
        const bin = bins[i]!;
        if (_intersects(a, b, bin.charStart, bin.charEnd)) bin.gtCount += 1;
      }
    }
  }
  let maxConsensus = 0;
  for (const b of bins) if (b.consensusCount > maxConsensus) maxConsensus = b.consensusCount;
  return { bins, maxConsensus };
}

export function DocCoverageGutter({
  totalChars,
  consensusSpans,
  gtSpans = null,
  selectedWindow = null,
  scrollRef,
  binCount = 150,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [height, setHeight] = useState(0);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  // Track the SVG container height so the viewBox + bin Y positions stay
  // aligned with the actual rendered pixel height. ResizeObserver wins
  // over a one-shot useEffect because the panel is inside a flex column
  // that resizes when the inspector pane reshuffles.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const r = el.getBoundingClientRect();
      setHeight(r.height);
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { bins, maxConsensus } = useMemo(
    () => _computeBins(totalChars, consensusSpans, gtSpans, binCount),
    [totalChars, consensusSpans, gtSpans, binCount],
  );

  // GT track only renders when GT is non-empty for this doc — null and
  // [] both suppress it. (Null = no GT loaded; [] = GT loaded with zero
  // mentions on this doc.)
  const showGtTrack = !!gtSpans && gtSpans.length > 0;
  const gtTotal = showGtTrack ? gtSpans!.length : 0;

  const safeTotal = Math.max(1, totalChars);

  // Selected-window marker → bin range. We pin it to whole-bin edges so
  // the marker rectangle aligns with the underlying density bars.
  const selectedMarker = useMemo(() => {
    if (!selectedWindow) return null;
    const { start, end } = selectedWindow;
    if (start == null || end == null || end <= start) return null;
    const stride = safeTotal / binCount;
    const firstBin = Math.max(0, Math.floor(start / stride));
    const lastBin = Math.min(binCount - 1, Math.floor((end - 1) / stride));
    if (lastBin < firstBin) return null;
    return { firstBin, lastBin };
  }, [selectedWindow, binCount, safeTotal]);

  // Click → scroll the doc-text scroller so the corresponding char
  // position is roughly at the top. Mirrors onMinimapClick on the left
  // chunk minimap.
  const onGutterClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = scrollRef.current;
    if (!el) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const ratio = Math.max(0, Math.min(1, y / Math.max(1, rect.height)));
    el.scrollTo({ top: el.scrollHeight * ratio, behavior: "smooth" });
  };

  // SVG geometry. Use a 12px-wide viewBox; bin height is computed from
  // measured container height so each bin is exactly 1 / binCount of the
  // visible track. Falls back to 1px before the first measure so the
  // SVG isn't degenerate before mount.
  const W = 12;
  const H = Math.max(1, height);
  const binH = H / binCount;

  // Inner amber stripe sits on top of the cyan layer, narrower and
  // centred so the cyan density still reads through on either side.
  const GT_STRIPE_W = 4;
  const GT_STRIPE_X = (W - GT_STRIPE_W) / 2;

  return (
    <div
      ref={containerRef}
      data-testid="doc-coverage-gutter"
      onClick={onGutterClick}
      className="w-3 flex-shrink-0 bg-zinc-900/60 border-l border-white/5 cursor-pointer relative"
      title="Coverage gutter"
    >
      <svg
        width={W}
        height="100%"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="block"
      >
        {/* Density layer */}
        {bins.map((bin, i) => {
          const opacity = maxConsensus > 0 ? bin.consensusCount / maxConsensus : 0;
          if (opacity <= 0) return null;
          return (
            <rect
              key={`density-${i}`}
              x={0}
              y={i * binH}
              width={W}
              height={Math.max(0.5, binH)}
              fill="rgb(34 211 238)"
              fillOpacity={Math.max(0.15, opacity * 0.8)}
            />
          );
        })}

        {/* GT recall-hole track (amber). Only when GT is loaded with > 0
            mentions. A bin is a "hole" iff GT has ≥ 1 mention here AND
            consensus has 0 mentions in the same bin. */}
        {showGtTrack && (
          <g data-testid="doc-coverage-gt-track">
            {bins.map((bin, i) => {
              if (bin.gtCount <= 0 || bin.consensusCount > 0) return null;
              return (
                <rect
                  key={`gt-${i}`}
                  x={GT_STRIPE_X}
                  y={i * binH}
                  width={GT_STRIPE_W}
                  height={Math.max(0.5, binH)}
                  fill="rgb(245 158 11)"
                  fillOpacity={0.85}
                />
              );
            })}
          </g>
        )}

        {/* Selected-window marker — saturated cyan over the bin range. */}
        {selectedMarker && (
          <rect
            data-testid="doc-coverage-selected-window-marker"
            x={0}
            y={selectedMarker.firstBin * binH}
            width={W}
            height={Math.max(1, (selectedMarker.lastBin - selectedMarker.firstBin + 1) * binH)}
            fill="rgb(34 211 238)"
            fillOpacity={0.9}
            stroke="rgb(165 243 252)"
            strokeWidth={0.5}
          />
        )}
      </svg>

      {/* Hover hit-rects — one per bin so even thin/empty bars are
          hoverable. Each is a single Tooltip trigger so we get the
          catalyst-ui tooltip surface treatment; without these the
          opacity-0 bars would have no pointer target at all. */}
      <div className="absolute inset-0 flex flex-col" aria-hidden="true">
        {bins.map((bin, i) => {
          const tip = (() => {
            const range = `chars ${bin.charStart.toLocaleString()}–${bin.charEnd.toLocaleString()}`;
            const mentions = `${bin.consensusCount} mention${bin.consensusCount === 1 ? "" : "s"}`;
            if (showGtTrack) {
              const missed = bin.gtCount > 0 && bin.consensusCount === 0 ? bin.gtCount : 0;
              const gtPart = `GT: ${gtTotal} (${missed} missed)`;
              return `${range} · ${mentions} · ${gtPart}`;
            }
            return `${range} · ${mentions}`;
          })();
          return (
            <Tooltip key={`hit-${i}`}>
              <TooltipTrigger asChild>
                <div
                  data-testid={`doc-coverage-bin-${i}`}
                  data-bin-consensus-count={bin.consensusCount}
                  data-bin-gt-count={bin.gtCount}
                  onMouseEnter={() => setHoverIdx(i)}
                  onMouseLeave={() => setHoverIdx((cur) => (cur === i ? null : cur))}
                  className={`flex-1 ${hoverIdx === i ? "ring-1 ring-cyan-300/60" : ""}`}
                  style={{ minHeight: 1 }}
                />
              </TooltipTrigger>
              <TooltipContent side="left" sideOffset={4} className={TOOLTIP_CLS}>
                {tip}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </div>
  );
}
