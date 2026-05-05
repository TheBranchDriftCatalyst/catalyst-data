/**
 * ConsensusDetail — right-pane panel rendered by StateInspector when the
 * selected chunk_id ends with ":_consensus".
 *
 * Reads the four consensus event types emitted by Phase B's ConsensusNode
 * (consensus_started, mention_decision, mention_rejected, consensus_completed)
 * and renders:
 *   - Header summary (encoder count, accepted/rejected totals, span
 *     disagreement rate) from consensus_completed + consensus_started.
 *   - Accepted mentions table sorted by vote_count desc (toggleable to
 *     canonical_type or mean_confidence).
 *   - Rejected section collapsed by default.
 */

import { useMemo, useState } from "react";

import type {
  ConsensusCompletedDetails,
  ConsensusStartedDetails,
  MentionDecisionDetails,
  MentionRejectedDetails,
  RunEvent,
} from "@/types/benchmark";
import { useActiveGroundTruth, useRunReport } from "@/hooks/useRunReport";
import { useTrendData } from "@/hooks/useTrendData";
import { matchesGtMention } from "@/lib/gt-match";

import { MentionTable, type GtMatch, type Mention } from "./MentionTable";
import { F1Strip, type F1Comparison, type F1Scores } from "./F1Strip";
import { EncoderCovoteMatrix, type EncoderCovoteFilter } from "./EncoderCovoteMatrix";
import { typeBadgeClass } from "./_mentionStyles";
import { TrendSparkline } from "../TrendSparkline";
import { DeepLinkButton } from "./DeepLinkButton";

interface Props {
  chunkId: string;
  events: RunEvent[];
  /** Active run id — required for the F1 strip + GT chips. ``null`` when
   *  no runs exist; the strip and chips skip render in that case. */
  runId: string | null;
  /** Gap #8 — selection-preserving run jump from the trend sparkline. */
  onJumpRun?: (runId: string) => void;
}

type SortKey = "votes" | "type" | "confidence";
type CovoteMode = "accepted" | "all";

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] uppercase text-zinc-600 mb-1 tracking-wide">{children}</div>;
}

function acceptedToMention(d: MentionDecisionDetails): Mention {
  // d may carry consensus span fields; keep them on the row so the
  // GT-matcher can use them. Cast through unknown — the typed bench
  // schema doesn't enumerate every cluster field, but at runtime the
  // span_start/span_end land on accepted decisions.
  const dx = d as unknown as {
    span_start?: number | null;
    span_end?: number | null;
  };
  return {
    text: d.text,
    type: d.canonical_type,
    vote: { count: d.vote_count, total: d.n_encoders },
    confidence: d.mean_confidence,
    spanProvenance: d.span_provenance,
    spanDisagreement: d.span_disagreement_chars,
    typeVotes: d.type_votes,
    span_start: dx.span_start ?? null,
    span_end: dx.span_end ?? null,
    variant: "accepted",
  };
}

function rejectedToMention(d: MentionRejectedDetails): Mention {
  const dx = d as unknown as {
    canonical_type?: string;
    span_start?: number | null;
    span_end?: number | null;
  };
  return {
    text: d.text,
    type: dx.canonical_type,
    vote: { count: d.vote_count, total: d.n_encoders },
    quorumNeeded: d.quorum,
    rejectionReason: d.reason,
    span_start: dx.span_start ?? null,
    span_end: dx.span_end ?? null,
    // Gap #9 — pass the cluster's source_models through to the row so
    // MentionTable can render the inline `from: <encoder>` / `from: <N>
    // encoders` chip. Legacy rejected events predating Gap #9 don't
    // carry the field; we forward `undefined` and the chip is omitted.
    sourceModels: d.source_models,
    variant: "rejected",
  };
}

// ── Main component ────────────────────────────────────────────────────────────

export function ConsensusDetail({ chunkId, events, runId, onJumpRun }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("votes");
  const [rejectedOpen, setRejectedOpen] = useState(false);
  // Gap #8 — last-10-runs trend for the consensus accepted count. Counts
  // don't depend on GT, so the smoke render works on bench fixtures with
  // GT mention_count=0.
  const docIdForTrend = chunkId.endsWith(":_consensus")
    ? chunkId.slice(0, -":_consensus".length)
    : chunkId;
  const { points: trendPoints } = useTrendData({
    axis: "doc",
    metric: "consensus_accepted_count",
    docId: docIdForTrend,
  });
  // Encoder co-vote matrix state (Gap #2). Filter is null until the user
  // clicks a cell; when set, the accepted-mentions table dims rows that
  // don't satisfy the filter (rows are kept in place — sort still applies
  // to dimmed rows so the operator doesn't lose spatial context).
  const [covoteMode, setCovoteMode] = useState<CovoteMode>("accepted");
  const [covoteFilter, setCovoteFilter] = useState<EncoderCovoteFilter | null>(null);
  const { data: report } = useRunReport(runId);
  const { data: gtList = [] } = useActiveGroundTruth();

  // Filter to only this consensus chunk's events
  const consensusEvents = useMemo(
    () => events.filter((e) => e.chunk_id === chunkId),
    [events, chunkId],
  );

  const startedEvent = useMemo(
    () => consensusEvents.find((e) => e.node_name === "consensus_started"),
    [consensusEvents],
  );

  const completedEvent = useMemo(
    () => consensusEvents.find((e) => e.node_name === "consensus_completed"),
    [consensusEvents],
  );

  const startedDetails = (startedEvent?.details ?? {}) as Partial<ConsensusStartedDetails>;
  const completedDetails = (completedEvent?.details ?? {}) as Partial<ConsensusCompletedDetails>;

  const accepted = useMemo<MentionDecisionDetails[]>(
    () =>
      consensusEvents
        .filter((e) => e.node_name === "mention_decision")
        .map((e) => e.details as unknown as MentionDecisionDetails),
    [consensusEvents],
  );

  const rejected = useMemo<MentionRejectedDetails[]>(
    () =>
      consensusEvents
        .filter((e) => e.node_name === "mention_rejected")
        .map((e) => e.details as unknown as MentionRejectedDetails),
    [consensusEvents],
  );

  const sortedAccepted = useMemo<MentionDecisionDetails[]>(() => {
    const copy = [...accepted];
    if (sortKey === "votes") {
      copy.sort((a, b) => b.vote_count - a.vote_count || b.mean_confidence - a.mean_confidence);
    } else if (sortKey === "type") {
      copy.sort(
        (a, b) => a.canonical_type.localeCompare(b.canonical_type) || b.vote_count - a.vote_count,
      );
    } else {
      copy.sort((a, b) => b.mean_confidence - a.mean_confidence);
    }
    return copy;
  }, [accepted, sortKey]);

  // Lookup: (text|canonical_type) → source_models. The MentionTable rows
  // it produces have lost ``source_models`` (they're not part of the
  // shared Mention shape), so the dim predicate re-keys on (text + type)
  // — same shape used by EncoderCovoteMatrix's mention key, minus the
  // span_start slot which isn't available on either side at runtime.
  const sourceModelsByRow = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const d of accepted) {
      m.set(`${d.text}|${d.canonical_type}`, d.source_models ?? []);
    }
    return m;
  }, [accepted]);

  const dimAcceptedRow = useMemo<((row: Mention) => boolean) | undefined>(() => {
    if (!covoteFilter) return undefined;
    return (row: Mention) => {
      const sm = sourceModelsByRow.get(`${row.text}|${row.type ?? ""}`) ?? [];
      if (covoteFilter.type === "pair") {
        const [a, b] = covoteFilter.encoders;
        return !(sm.includes(a) && sm.includes(b));
      }
      // Lone filter: row stays bright iff it was voted by exactly that encoder.
      const [only] = covoteFilter.encoders;
      return !(sm.length === 1 && sm[0] === only);
    };
  }, [covoteFilter, sourceModelsByRow]);

  // Extract doc_id from chunkId (strip the trailing ":_consensus")
  const docId = chunkId.endsWith(":_consensus") ? chunkId.slice(0, -":_consensus".length) : chunkId;

  // Pull consensus + best-encoder F1 from the per-run report. The
  // benchmark harness writes the ensemble entry under name=="ensemble";
  // some older reports use "consensus" — accept either.
  const f1Scores = useMemo<F1Scores | null>(() => {
    if (!report || !report.ground_truth?.available) return null;
    const entry =
      report.models.find((m) => m.name === "ensemble" || m.name === "consensus") ?? null;
    const s = entry?.scores;
    if (!s || s.mention_strict_f1 === undefined) return null;
    return {
      precision: s.mention_strict_precision ?? 0,
      recall: s.mention_strict_recall ?? 0,
      strict_f1: s.mention_strict_f1 ?? 0,
      partial_f1: s.mention_relaxed_f1,
    };
  }, [report]);

  const f1Comparison = useMemo<F1Comparison | undefined>(() => {
    if (!report || !f1Scores) return undefined;
    const encoderEntries = report.models.filter((m) => m.type === "encoder");
    if (encoderEntries.length === 0) return undefined;
    const best = Math.max(...encoderEntries.map((m) => m.scores?.mention_strict_f1 ?? 0));
    return {
      delta: f1Scores.strict_f1 - best,
      baselineLabel: "vs best encoder",
    };
  }, [report, f1Scores]);

  // GT-classifier — classifies a row as "in" (matches a GT mention) /
  // "out" (does not) / "unknown" (no GT loaded for this run). The chip
  // renders only when the run advertises ground_truth.available AND the
  // active GT has at least one mention in scope of this doc — without
  // the doc-scope check we'd spuriously show ✗ on every row when the
  // active GT covers a different domain.
  const gtMatchesFn = useMemo<((row: Mention) => GtMatch) | undefined>(() => {
    if (!report?.ground_truth?.available) return undefined;
    if (gtList.length === 0) return undefined;
    // Only count GT rows scoped to this consensus's doc — otherwise a
    // run with a single-doc GT would mark every other doc's mentions
    // as ✗. ``doc_id`` may be undefined if the GT didn't carry it; we
    // fall back to "any GT row" in that case so we still surface chips.
    const scoped = gtList.filter((g) => !g.doc_id || g.doc_id === docId);
    if (scoped.length === 0) return undefined;
    return (row: Mention): GtMatch => {
      const ok = matchesGtMention(
        {
          text: row.text,
          mention_type: row.type,
          span_start: row.span_start ?? null,
          span_end: row.span_end ?? null,
          doc_id: docId,
        },
        scoped,
      );
      return ok ? "in" : "out";
    };
  }, [report, gtList, docId]);

  // Derive encoder list from source_models across all accepted decisions
  const encoderNames = useMemo(() => {
    const seen = new Set<string>();
    for (const d of accepted) {
      for (const m of d.source_models) seen.add(m);
    }
    return [...seen].sort();
  }, [accepted]);

  const nEncoders =
    startedDetails.n_encoders ??
    (completedDetails.accepted_count !== undefined ? (accepted[0]?.n_encoders ?? 0) : 0);

  const acceptedCount = completedDetails.accepted_count ?? accepted.length;
  const rejectedCount = completedDetails.rejected_count ?? rejected.length;
  const disagreeRate = completedDetails.span_disagreement_rate ?? null;
  const typeDistribution = completedDetails.type_distribution ?? {};

  if (consensusEvents.length === 0) {
    return (
      <div className="p-4 font-mono text-xs text-zinc-600">
        No consensus events yet for <span className="text-zinc-400">{chunkId}</span>.
      </div>
    );
  }

  return (
    <div data-testid="consensus-detail" className="p-3 space-y-4">
      {/* ── Header ── */}
      <div className="space-y-1">
        <div className="flex items-start justify-between gap-2">
          <div className="font-mono text-[11px] text-zinc-300 font-semibold">
            Consensus — {docId}
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            <DeepLinkButton testidPrefix="consensus" panelName="consensus" />
            <TrendSparkline
              points={trendPoints}
              metric="consensus_accepted_count"
              currentRunId={runId}
              onSelectRun={(id) => onJumpRun?.(id)}
              trend="up-good"
            />
          </div>
        </div>

        {/* Encoder list */}
        {encoderNames.length > 0 && (
          <div className="font-mono text-[10px] text-zinc-500">
            encoders:{" "}
            <span className="text-zinc-400">{nEncoders > 0 ? nEncoders : encoderNames.length}</span>
            {" — "}
            <span className="text-zinc-400">{encoderNames.join(", ")}</span>
          </div>
        )}

        {/* F1 strip — only when the run has ground truth available. */}
        <F1Strip scores={f1Scores} comparison={f1Comparison} />

        {/* Accepted / rejected summary */}
        <div className="flex items-center gap-3 font-mono text-[11px] flex-wrap">
          <span>
            <span data-testid="consensus-accepted-count" className="text-emerald-300">
              {acceptedCount}
            </span>
            <span className="text-zinc-500"> accepted</span>
          </span>
          <span className="text-zinc-700">·</span>
          <span>
            <span data-testid="consensus-rejected-count" className="text-red-300">
              {rejectedCount}
            </span>
            <span className="text-zinc-500"> rejected</span>
          </span>
          {disagreeRate !== null && (
            <>
              <span className="text-zinc-700">·</span>
              <span>
                <span className={disagreeRate > 0.2 ? "text-amber-300" : "text-zinc-400"}>
                  {(disagreeRate * 100).toFixed(0)}%
                </span>
                <span className="text-zinc-500"> span disagreement</span>
              </span>
            </>
          )}
        </div>

        {/* Type distribution pills */}
        {Object.keys(typeDistribution).length > 0 && (
          <div className="flex flex-wrap gap-1 pt-0.5">
            {Object.entries(typeDistribution)
              .sort(([, a], [, b]) => b - a)
              .map(([type, count]) => (
                <span
                  key={type}
                  className={`px-1.5 py-0.5 rounded border text-[9.5px] font-mono ${typeBadgeClass(type)}`}
                >
                  {type}
                  <span className="ml-1 opacity-70">×{count}</span>
                </span>
              ))}
          </div>
        )}
      </div>

      {/* ── Encoder agreement matrix (Gap #2) ── */}
      {encoderNames.length >= 2 && (
        <details
          open={encoderNames.length >= 3}
          className="group [&>summary::-webkit-details-marker]:hidden"
        >
          <summary className="flex items-center gap-1.5 text-[10px] uppercase text-zinc-600 hover:text-zinc-400 transition-colors font-mono tracking-wide cursor-pointer list-none w-full">
            <span className="text-zinc-700 group-open:rotate-90 inline-block transition-transform">
              ▸
            </span>
            encoder agreement ({encoderNames.length}×{encoderNames.length})
          </summary>
          <div className="mt-2">
            <EncoderCovoteMatrix
              encoders={encoderNames}
              accepted={accepted}
              rejected={rejected}
              mode={covoteMode}
              activeFilter={covoteFilter}
              onFilterChange={setCovoteFilter}
              onModeChange={setCovoteMode}
            />
          </div>
        </details>
      )}

      {/* ── Accepted section ── */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <SectionTitle>accepted ({accepted.length})</SectionTitle>
          {accepted.length > 0 && (
            <div className="flex items-center gap-1 ml-auto">
              {(["votes", "type", "confidence"] as SortKey[]).map((k) => (
                <button
                  key={k}
                  data-testid={`consensus-sort-${k}`}
                  type="button"
                  onClick={() => setSortKey(k)}
                  className={`text-[9.5px] font-mono px-1.5 py-0.5 rounded transition-colors ${
                    sortKey === k
                      ? "bg-cyan-500/20 text-cyan-300"
                      : "text-zinc-600 hover:text-zinc-400"
                  }`}
                >
                  {k}
                </button>
              ))}
            </div>
          )}
        </div>

        <MentionTable
          rows={sortedAccepted.map(acceptedToMention)}
          columns={["text", "type", "vote", "conf", "span"]}
          emptyMessage="No accepted mentions yet."
          className="space-y-1 max-h-[50vh] overflow-y-auto pr-0.5"
          rowTestId="consensus-accepted-row"
          gtMatches={gtMatchesFn}
          rowDim={dimAcceptedRow}
        />
      </div>

      {/* ── Rejected section (collapsed by default) ── */}
      <div>
        <button
          data-testid="consensus-rejected-toggle"
          type="button"
          onClick={() => setRejectedOpen((v) => !v)}
          className="flex items-center gap-1.5 text-[10px] uppercase text-zinc-600 hover:text-zinc-400 transition-colors font-mono tracking-wide w-full text-left"
        >
          <span className="text-zinc-700">{rejectedOpen ? "▾" : "▸"}</span>
          rejected ({rejected.length})
        </button>

        {rejectedOpen && (
          <MentionTable
            rows={rejected.map(rejectedToMention)}
            columns={["text", "vote", "reason", "source"]}
            emptyMessage="No rejected mentions."
            className="mt-1 space-y-1 max-h-[30vh] overflow-y-auto pr-0.5"
            rowTestId="consensus-rejected-row"
            containerTestId="consensus-rejected-list"
            gtMatches={gtMatchesFn}
          />
        )}
      </div>
    </div>
  );
}
