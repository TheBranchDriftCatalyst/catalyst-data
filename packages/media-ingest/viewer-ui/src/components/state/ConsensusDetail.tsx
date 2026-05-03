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

interface Props {
  chunkId: string;
  events: RunEvent[];
}

type SortKey = "votes" | "type" | "confidence";

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Format type_votes dict as "PERSON×4, GPE×1" sorted by count desc. */
function fmtTypeVotes(tv: Record<string, number>): string {
  return Object.entries(tv)
    .sort(([, a], [, b]) => b - a)
    .map(([t, n]) => `${t}×${n}`)
    .join(", ");
}

/** Badge colour for canonical_type — matches existing mention pill palette. */
function typeBadgeClass(t: string): string {
  const key = t.toUpperCase();
  if (key === "PERSON") return "bg-blue-500/15 text-blue-200 border-blue-500/30";
  if (key === "ORG" || key === "ORGANIZATION")
    return "bg-violet-500/15 text-violet-200 border-violet-500/30";
  if (key === "GPE" || key === "LOCATION" || key === "LOC" || key === "FAC")
    return "bg-emerald-500/15 text-emerald-200 border-emerald-500/30";
  if (key === "DATE" || key === "TIME" || key === "TEMPORAL")
    return "bg-amber-500/15 text-amber-200 border-amber-500/30";
  return "bg-zinc-500/15 text-zinc-300 border-zinc-500/30";
}

function VoteFraction({ vote, total }: { vote: number; total: number }) {
  const pct = total > 0 ? (vote / total) * 100 : 0;
  const barClass = pct >= 80 ? "bg-emerald-400" : pct >= 50 ? "bg-cyan-400" : "bg-amber-400";
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-mono text-zinc-200">
        {vote}/{total}
      </span>
      <span className="inline-block w-8 h-1 rounded bg-white/10 overflow-hidden align-middle">
        <span className={`block h-full rounded ${barClass}`} style={{ width: `${pct}%` }} />
      </span>
    </span>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] uppercase text-zinc-600 mb-1 tracking-wide">{children}</div>;
}

function AcceptedRow({ d }: { d: MentionDecisionDetails }) {
  return (
    <div className="rounded border border-white/5 bg-white/[0.02] px-2 py-1.5 space-y-0.5 hover:bg-white/[0.04] transition-colors">
      <div className="flex items-center gap-2 font-mono text-[11px]">
        {/* text */}
        <span className="text-zinc-100 font-semibold min-w-0 truncate flex-1" title={d.text}>
          {d.text}
        </span>
        {/* type badge */}
        <span
          className={`px-1.5 py-0.5 rounded border text-[9.5px] font-mono flex-shrink-0 ${typeBadgeClass(d.canonical_type)}`}
        >
          {d.canonical_type}
        </span>
        {/* vote fraction */}
        <VoteFraction vote={d.vote_count} total={d.n_encoders} />
        {/* mean confidence */}
        <span className="text-zinc-500 text-[10px] font-mono flex-shrink-0">
          conf {d.mean_confidence.toFixed(2)}
        </span>
      </div>
      {/* type votes + span provenance */}
      <div className="flex items-center gap-2 font-mono text-[10px] text-zinc-500">
        <span className="truncate" title={fmtTypeVotes(d.type_votes)}>
          {fmtTypeVotes(d.type_votes)}
        </span>
        <span className="text-zinc-700">|</span>
        <span className="flex-shrink-0">
          span: <span className="text-zinc-400">{d.span_provenance}</span>
        </span>
        {d.span_disagreement_chars > 0 && (
          <>
            <span className="text-zinc-700">|</span>
            <span className="text-amber-400/80 flex-shrink-0">
              disagree {d.span_disagreement_chars}ch
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function RejectedRow({ d }: { d: MentionRejectedDetails }) {
  return (
    <div className="rounded border border-white/5 bg-white/[0.015] px-2 py-1.5 hover:bg-white/[0.03] transition-colors">
      <div className="flex items-center gap-2 font-mono text-[11px]">
        <span className="text-zinc-400 flex-1 truncate" title={d.text}>
          {d.text}
        </span>
        <span className="text-zinc-600 text-[10px] flex-shrink-0">
          {d.vote_count}/{d.n_encoders} (need {d.quorum})
        </span>
        <span className="px-1.5 py-0.5 rounded text-[9.5px] font-mono bg-red-500/10 text-red-300 border border-red-500/20 flex-shrink-0">
          {d.reason}
        </span>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function ConsensusDetail({ chunkId, events }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("votes");
  const [rejectedOpen, setRejectedOpen] = useState(false);

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

  // Extract doc_id from chunkId (strip the trailing ":_consensus")
  const docId = chunkId.endsWith(":_consensus") ? chunkId.slice(0, -":_consensus".length) : chunkId;

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
    <div className="p-3 space-y-4">
      {/* ── Header ── */}
      <div className="space-y-1">
        <div className="font-mono text-[11px] text-zinc-300 font-semibold">Consensus — {docId}</div>

        {/* Encoder list */}
        {encoderNames.length > 0 && (
          <div className="font-mono text-[10px] text-zinc-500">
            encoders:{" "}
            <span className="text-zinc-400">{nEncoders > 0 ? nEncoders : encoderNames.length}</span>
            {" — "}
            <span className="text-zinc-400">{encoderNames.join(", ")}</span>
          </div>
        )}

        {/* Accepted / rejected summary */}
        <div className="flex items-center gap-3 font-mono text-[11px] flex-wrap">
          <span>
            <span className="text-emerald-300">{acceptedCount}</span>
            <span className="text-zinc-500"> accepted</span>
          </span>
          <span className="text-zinc-700">·</span>
          <span>
            <span className="text-red-300">{rejectedCount}</span>
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

      {/* ── Accepted section ── */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <SectionTitle>accepted ({accepted.length})</SectionTitle>
          {accepted.length > 0 && (
            <div className="flex items-center gap-1 ml-auto">
              {(["votes", "type", "confidence"] as SortKey[]).map((k) => (
                <button
                  key={k}
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

        {accepted.length === 0 ? (
          <div className="text-[10px] font-mono text-zinc-600 px-1">No accepted mentions yet.</div>
        ) : (
          <div className="space-y-1 max-h-[50vh] overflow-y-auto pr-0.5">
            {sortedAccepted.map((d, i) => (
              <AcceptedRow key={`${d.text}-${i}`} d={d} />
            ))}
          </div>
        )}
      </div>

      {/* ── Rejected section (collapsed by default) ── */}
      <div>
        <button
          type="button"
          onClick={() => setRejectedOpen((v) => !v)}
          className="flex items-center gap-1.5 text-[10px] uppercase text-zinc-600 hover:text-zinc-400 transition-colors font-mono tracking-wide w-full text-left"
        >
          <span className="text-zinc-700">{rejectedOpen ? "▾" : "▸"}</span>
          rejected ({rejected.length})
        </button>

        {rejectedOpen && (
          <div className="mt-1 space-y-1 max-h-[30vh] overflow-y-auto pr-0.5">
            {rejected.length === 0 ? (
              <div className="text-[10px] font-mono text-zinc-600 px-1">No rejected mentions.</div>
            ) : (
              rejected.map((d, i) => <RejectedRow key={`${d.text}-${i}`} d={d} />)
            )}
          </div>
        )}
      </div>
    </div>
  );
}
