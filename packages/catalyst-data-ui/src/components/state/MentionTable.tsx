/**
 * MentionTable — single shared renderer for the "list of mentions" UX
 * that previously diverged across three callsites:
 *
 *   - ConsensusDetail.AcceptedRow / RejectedRow
 *   - NerEncoderDetail mention list (per-encoder)
 *   - StateInspector pruned_window cluster-mentions mini-table
 *
 * Each row is a `font-mono text-[11px]` card with `bg-white/[0.02]` styling.
 * The visible columns are driven by the `columns` prop so a callsite that
 * has no `vote_count` (encoder detail) can simply omit "vote" rather than
 * rendering empty cells.
 *
 * No new features (sorting, filtering, pagination) — the wrapping callsite
 * does its own sort and passes the already-ordered rows.
 */

import type { ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@thebranchdriftcatalyst/catalyst-ui";

import { fmtTypeVotes, typeBadgeClass } from "./_mentionStyles";

// Same solid-surface treatment used by Gap #4's pack histograms and the
// EncoderCovoteMatrix — keeps the multi-encoder source-chip tooltip
// readable against the panel background rather than see-through.
const TOOLTIP_CLS =
  "z-50 max-w-sm rounded-md border border-white/10 bg-surface-1 text-zinc-100 px-3 py-2 shadow-xl text-[11px] leading-relaxed font-mono whitespace-pre-line";

/**
 * Normalised row shape — superset of every existing callsite's needs.
 * All fields are optional except `text`, and unset cells render nothing
 * (rather than dashes) so the row stays compact.
 */
export interface Mention {
  text: string;
  /** canonical_type | mention_type | type — caller is responsible for picking. */
  type?: string;
  /** Consensus-only: accepted vote_count / total encoders. */
  vote?: { count: number; total: number };
  /** 0..1 confidence (consensus mean_confidence or per-encoder confidence). */
  confidence?: number;
  /** Encoder name that contributed the highest-confidence span. */
  spanProvenance?: string;
  /** Max char-offset difference between chosen span and any other cluster span. */
  spanDisagreement?: number;
  /** Per-type vote counts, e.g. { PERSON: 4, GPE: 1 } — rendered as PERSON×4, GPE×1. */
  typeVotes?: Record<string, number>;
  /** Rejected-row only: human-readable rejection reason. */
  rejectionReason?: string;
  /** Rejected-row only: the quorum threshold (so the operator can see how close). */
  quorumNeeded?: number;
  /**
   * Optional visual variant — `accepted` (default), `rejected` (muted text +
   * red reason badge), or `muted` (encoder list, slightly less weight).
   */
  variant?: "accepted" | "rejected" | "muted";
  /** Span fields used by the GT-matcher; not rendered directly. */
  span_start?: number | null;
  span_end?: number | null;
  /**
   * Gap #9 — encoders that voted for this mention (rejected-row context).
   * When the row is rendered with `columns` containing `"source"` and this
   * array is non-empty, an inline `from: <encoder>` (lone) or
   * `from: <N> encoders` (multi) chip renders after the reason badge.
   * Ordering is preserved (list, not set) so it round-trips identically
   * from the audit-event payload.
   */
  sourceModels?: string[];
}

/** GT classification for a row.
 *  - "in" — span is in GT (TP on accepted; FN on rejected)
 *  - "out" — span is NOT in GT (FP on accepted; TN on rejected)
 *  - "unknown" / undefined — no GT loaded for this run, render no chip
 */
export type GtMatch = "in" | "out" | "unknown";

export type MentionColumn =
  | "text"
  | "type"
  | "vote"
  | "conf"
  | "span"
  | "reason"
  /** Gap #9 — `from: <encoder>` / `from: <N> encoders` chip on rejected rows. */
  | "source";

interface Props {
  rows: Mention[];
  /** Which columns to render. Defaults to ["text","type","vote","conf","span"]. */
  columns?: MentionColumn[];
  /** Empty-state message when rows is empty. */
  emptyMessage?: string;
  /** Optional className applied to the outer scroll container. */
  className?: string;
  /** Optional ``data-testid`` applied to each row card so callsites can
   *  expose distinct selectors (e.g. ``consensus-accepted-row`` vs
   *  ``ner-encoder-mention-row``) without forking this component. */
  rowTestId?: string;
  /** Optional ``data-testid`` for the outer scroll container — used by the
   *  consensus rejected list assertion (§2.7). */
  containerTestId?: string;
  /** Optional GT classifier — per-row "in" / "out" / "unknown". When
   *  provided AND the row is classified ``in`` or ``out``, a tiny GT chip
   *  renders next to the type badge. Encoder-side callers do NOT pass
   *  this — the GT chip only shows on the consensus accepted+rejected
   *  tables (the prediction layer that consensus produces). */
  gtMatches?: (row: Mention) => GtMatch;
  /** Optional per-row dimming predicate — when truthy the row card is
   *  rendered with ``opacity-30`` so callers can implement filter-by-
   *  context without removing rows from the list. Used by the consensus
   *  accepted-list when an encoder co-vote filter is active. */
  rowDim?: (row: Mention) => boolean;
}

const DEFAULT_COLUMNS: MentionColumn[] = ["text", "type", "vote", "conf", "span"];

function VoteFraction({ vote, total, testId }: { vote: number; total: number; testId?: string }) {
  const pct = total > 0 ? (vote / total) * 100 : 0;
  const barClass = pct >= 80 ? "bg-emerald-400" : pct >= 50 ? "bg-cyan-400" : "bg-amber-400";
  return (
    <span className="inline-flex items-center gap-1" data-testid={testId}>
      <span className="font-mono text-zinc-200">
        {vote}/{total}
      </span>
      <span className="inline-block w-8 h-1 rounded bg-white/10 overflow-hidden align-middle">
        <span className={`block h-full rounded ${barClass}`} style={{ width: `${pct}%` }} />
      </span>
    </span>
  );
}

/**
 * Gap #9 — inline chip that surfaces which encoders voted for a rejected
 * mention. Two visual variants:
 *   - `sourceModels.length === 1` → cyan "from: gliner-pii" (lone-voter
 *     signal — asymmetric coverage; `per_type_quorum` candidate).
 *   - `sourceModels.length > 1`   → zinc "from: 2 encoders" with a hover
 *     tooltip listing the full encoder set in original order.
 *   - empty / missing             → no chip (legacy events pre-Gap #9).
 *
 * The full encoder list lives in the tooltip (multi case) so the chip
 * itself stays compact next to the reason badge.
 */
function SourceChip({ encoders }: { encoders: string[] }) {
  if (!encoders || encoders.length === 0) return null;
  const count = encoders.length;
  if (count === 1) {
    const only = encoders[0]!;
    return (
      <span
        data-testid="mention-source-chip"
        data-source-count="1"
        className="px-1.5 py-0.5 rounded text-[9.5px] font-mono bg-cyan-500/15 text-cyan-200 border border-cyan-500/40 flex-shrink-0"
        title={`from: ${only}`}
      >
        from: {only}
      </span>
    );
  }
  // Multi-encoder: list each encoder name in the tooltip body, preserving
  // the audit-event ordering (no sort, no truncation, no dedupe).
  // Per-encoder vote counts aren't available on rejected events — list names only.
  const tooltipText = encoders.join(", ");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid="mention-source-chip"
          data-source-count={String(count)}
          className="px-1.5 py-0.5 rounded text-[9.5px] font-mono bg-zinc-700/40 text-zinc-300 border border-zinc-600/40 flex-shrink-0 cursor-help"
        >
          from: {count} encoders
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" sideOffset={4} className={TOOLTIP_CLS}>
        {tooltipText}
      </TooltipContent>
    </Tooltip>
  );
}

function GtChip({ kind, variant }: { kind: GtMatch; variant: Mention["variant"] }) {
  if (kind === "unknown") return null;
  // Semantics by row variant:
  //   accepted + in  → TP   (cyan ✓ "in GT")
  //   accepted + out → FP   (red ✗ "not in GT")
  //   rejected + in  → FN   (cyan ✓ "in GT" — consensus rejected something real)
  //   rejected + out → TN   (red ✗ "not in GT" — consensus correctly rejected noise)
  const isIn = kind === "in";
  const className = isIn
    ? "px-1 py-0 rounded text-[10px] font-mono bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 flex-shrink-0"
    : "px-1 py-0 rounded text-[10px] font-mono bg-red-500/15 text-red-300 border border-red-500/30 flex-shrink-0";
  // Title text differs per row variant so the operator gets the
  // right "what does this mean" hover.
  const title = isIn
    ? variant === "rejected"
      ? "in GT — false negative (consensus rejected a true mention)"
      : "in GT — true positive"
    : variant === "rejected"
      ? "not in GT — true negative (consensus correctly rejected)"
      : "not in GT — false positive";
  return (
    <span data-testid="mention-gt-chip" data-gt-match={kind} className={className} title={title}>
      {isIn ? "✓" : "✗"}
    </span>
  );
}

function MentionRow({
  row,
  columns,
  testId,
  gtMatch,
  dim,
}: {
  row: Mention;
  columns: MentionColumn[];
  testId?: string;
  gtMatch?: GtMatch;
  dim?: boolean;
}) {
  const variant = row.variant ?? "accepted";
  const has = (c: MentionColumn) => columns.includes(c);

  // Bg / border palette per variant — matches the original AcceptedRow /
  // RejectedRow look so this drop-in migration is visually identical.
  const dimClass = dim ? " opacity-30" : "";
  const cardClass =
    (variant === "rejected"
      ? "rounded border border-white/5 bg-white/[0.015] px-2 py-1.5 hover:bg-white/[0.03] transition-colors"
      : "rounded border border-white/5 bg-white/[0.02] px-2 py-1.5 space-y-0.5 hover:bg-white/[0.04] transition-colors") +
    dimClass;

  const textClass =
    variant === "rejected"
      ? "text-zinc-400 flex-1 truncate"
      : variant === "muted"
        ? "text-zinc-200 flex-1 truncate"
        : "text-zinc-100 font-semibold min-w-0 truncate flex-1";

  // Decide whether to render the secondary metadata line (typeVotes / span /
  // disagreement). Only consensus AcceptedRow had this; rejected + encoder
  // rows skip it.
  const showSecondLine =
    variant !== "rejected" &&
    has("span") &&
    (!!row.typeVotes || row.spanProvenance != null || (row.spanDisagreement ?? 0) > 0);

  // For consensus rows the spec expects inner ``consensus-row-type`` /
  // ``consensus-row-votes`` testids. Derive them by replacing the ``-row``
  // suffix on the parent rowTestId so naming stays in lockstep.
  const innerTypeTestId = testId && testId.endsWith("-row") ? `${testId}-type` : undefined;
  const innerVotesTestId = testId && testId.endsWith("-row") ? `${testId}-votes` : undefined;

  return (
    <div className={cardClass} data-testid={testId}>
      <div className="flex items-center gap-2 font-mono text-[11px]">
        {/* text */}
        {has("text") && (
          <span className={textClass} title={row.text}>
            {row.text}
          </span>
        )}

        {/* type badge */}
        {has("type") && row.type && (
          <span
            data-testid={innerTypeTestId}
            className={`px-1.5 py-0.5 rounded border text-[9.5px] font-mono flex-shrink-0 ${typeBadgeClass(row.type)}`}
          >
            {row.type}
          </span>
        )}

        {/* GT chip — renders only when caller supplies a classifier and
         *  the row is classified (i.e. a GT exists for this run). */}
        {gtMatch && gtMatch !== "unknown" && <GtChip kind={gtMatch} variant={variant} />}

        {/* vote fraction */}
        {has("vote") && row.vote && (
          <>
            {variant === "rejected" ? (
              <span
                data-testid={innerVotesTestId}
                className="text-zinc-600 text-[10px] flex-shrink-0"
              >
                {row.vote.count}/{row.vote.total}
                {row.quorumNeeded != null ? ` (need ${row.quorumNeeded})` : ""}
              </span>
            ) : (
              <VoteFraction
                vote={row.vote.count}
                total={row.vote.total}
                testId={innerVotesTestId}
              />
            )}
          </>
        )}

        {/* confidence */}
        {has("conf") && row.confidence != null && (
          <span className="text-zinc-500 text-[10px] font-mono flex-shrink-0">
            {variant === "muted"
              ? `${(row.confidence * 100).toFixed(0)}%`
              : `conf ${row.confidence.toFixed(2)}`}
          </span>
        )}

        {/* rejection reason badge — only on rejected variant */}
        {has("reason") && row.rejectionReason && variant === "rejected" && (
          <span className="px-1.5 py-0.5 rounded text-[9.5px] font-mono bg-red-500/10 text-red-300 border border-red-500/20 flex-shrink-0">
            {row.rejectionReason}
          </span>
        )}

        {/* Gap #9 — encoder-source chip on rejected rows. Only renders
         *  when the callsite opts into the "source" column AND the row
         *  carries a non-empty sourceModels list (legacy rejected events
         *  predating Gap #9 lack the field — no placeholder). */}
        {has("source") && row.sourceModels && row.sourceModels.length > 0 && (
          <SourceChip encoders={row.sourceModels} />
        )}
      </div>

      {showSecondLine && (
        <div className="flex items-center gap-2 font-mono text-[10px] text-zinc-500">
          {row.typeVotes && Object.keys(row.typeVotes).length > 0 && (
            <span className="truncate" title={fmtTypeVotes(row.typeVotes)}>
              {fmtTypeVotes(row.typeVotes)}
            </span>
          )}
          {row.spanProvenance && (
            <>
              {row.typeVotes && Object.keys(row.typeVotes).length > 0 && (
                <span className="text-zinc-700">|</span>
              )}
              <span className="flex-shrink-0">
                span: <span className="text-zinc-400">{row.spanProvenance}</span>
              </span>
            </>
          )}
          {(row.spanDisagreement ?? 0) > 0 && (
            <>
              <span className="text-zinc-700">|</span>
              <span className="text-amber-400/80 flex-shrink-0">
                disagree {row.spanDisagreement}ch
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function MentionTable({
  rows,
  columns = DEFAULT_COLUMNS,
  emptyMessage,
  className,
  rowTestId,
  containerTestId,
  gtMatches,
  rowDim,
}: Props): ReactNode {
  if (rows.length === 0) {
    return emptyMessage ? (
      <div className="text-[10px] font-mono text-zinc-600 px-1">{emptyMessage}</div>
    ) : null;
  }
  return (
    <div className={className ?? "space-y-1"} data-testid={containerTestId}>
      {rows.map((row, i) => (
        <MentionRow
          key={`${row.text}-${i}`}
          row={row}
          columns={columns}
          testId={rowTestId}
          gtMatch={gtMatches ? gtMatches(row) : undefined}
          dim={rowDim ? rowDim(row) : false}
        />
      ))}
    </div>
  );
}
