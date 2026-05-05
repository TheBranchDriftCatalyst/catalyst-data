/**
 * PathologyRollup — document-level diagnostic signals.
 *
 * Renders a compact health card below the document source text showing 5 computed
 * pathology signals:
 *   1. Consensus rejection ratio — accepted / (accepted + rejected)
 *   2. Pack prune ratio — pruned / (kept + pruned) windows
 *   3. Encoder disagreement — (max - min) mention_count / mean (normalized)
 *   4. Confidence skew — mean confidence < 0.5 or null encoding
 *   5. Zero-pipeline — status="completed" but details empty
 *
 * Each row is color-coded: green (ok), amber (soft-flag), rose (hard-flag).
 */

import { useMemo } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@thebranchdriftcatalyst/catalyst-ui";
import type { ConsensusCompletedDetails, RunEvent } from "@/types/benchmark";

interface PathologySignal {
  id: string;
  name: string;
  status: "ok" | "soft" | "hard";
  value: string;
  threshold: string;
  hint: string;
}

interface Props {
  events: RunEvent[];
  docId: string;
}

// ── Thresholds (tunable) ────────────────────────────────────────────────────
// These are defensible boundaries between healthy and pathological states.
// Hard threshold = ~50% above soft (e.g. consensus reject soft=0.30, hard=0.45).

const THRESHOLDS = {
  // Consensus rejection: fraction of mentions that failed to reach quorum.
  // Soft: >30% rejected suggests disagreement; Hard: >45% suggests severe issues.
  consensusRejectSoft: 0.3,
  consensusRejectHard: 0.45,

  // Pack prune ratio: fraction of windows pruned before SPO.
  // Soft: >50% pruned suggests over-aggressive windowing; Hard: >75% lost.
  packPruneSoft: 0.5,
  packPruneHard: 0.75,

  // Encoder disagreement: (max - min) / mean normalized to 0–1.
  // Clamped to 0 when total mention count < 5 (avoid noise).
  // Soft: >0.3 (30% relative variance); Hard: >0.5 (50% relative variance).
  encoderDisagreementSoft: 0.3,
  encoderDisagreementHard: 0.5,

  // Confidence skew: mean < 0.5 or all-null indicates encoding uncertainty.
  // Soft: mean between 0.5–0.6; Hard: mean < 0.5 or all null.
  confidenceSkewSoft: 0.6,

  // Zero-pipeline: any node with status="completed" but details={} or missing.
  // This is a binary check (no soft/hard distinction) — either the node ran
  // and produced output or it didn't.
};

/** Extract encoder names and their mention counts (used for disagreement calc). */
function _extractEncoderMentionCounts(events: RunEvent[], docId: string): Record<string, number> {
  const counts: Record<string, number> = {};
  const docPrefix = `${docId}:_ner_`;
  for (const e of events) {
    if (!e.chunk_id?.startsWith(docPrefix)) continue;
    if (e.node_name !== "chunk_extracted" || e.status !== "completed") continue;
    const encoder = e.chunk_id.slice(docPrefix.length);
    const details = (e.details ?? {}) as { mentions?: unknown[] };
    const mentionCount = Array.isArray(details.mentions) ? details.mentions.length : 0;
    counts[encoder] = (counts[encoder] ?? 0) + mentionCount;
  }
  return counts;
}

/** Compute consensus rejection ratio. */
function _computeConsensusRejection(events: RunEvent[], docId: string): number {
  const consensusEvent = events.find(
    (e) => e.node_name === "consensus_completed" && e.doc_id === docId,
  );
  if (!consensusEvent) return 0;
  const details = (consensusEvent.details ?? {}) as unknown as ConsensusCompletedDetails;
  const accepted = details.accepted_count ?? 0;
  const rejected = details.rejected_count ?? 0;
  const total = accepted + rejected;
  return total > 0 ? rejected / total : 0;
}

/** Compute pack prune ratio. */
function _computePackPruneRatio(events: RunEvent[], docId: string): number {
  const docPrefix = `${docId}:`;
  const packEvent = events.find(
    (e) =>
      e.node_name === "pack_evidence" &&
      e.status === "completed" &&
      (e.doc_id === docId || e.chunk_id?.startsWith(docPrefix)),
  );
  if (!packEvent) return 0;

  const details = (packEvent.details ?? {}) as Record<string, unknown>;
  const keptWindows = (details.kept_windows as Array<Record<string, unknown>>) ?? [];
  const kept = keptWindows.length;

  // Pruned windows are sparse (emit as evidence_window_pruned events).
  const prunedSet = new Set<string>();
  for (const e of events) {
    if (e.node_name !== "evidence_window_pruned") continue;
    const wid = ((e.details ?? {}) as { window_id?: string }).window_id;
    if (wid) prunedSet.add(wid);
  }
  const pruned = prunedSet.size;

  const total = kept + pruned;
  return total > 0 ? pruned / total : 0;
}

/** Compute encoder disagreement: (max - min) / mean, clamped when total < 5. */
function _computeEncoderDisagreement(counts: Record<string, number>): number {
  const values = Object.values(counts);
  if (values.length === 0) return 0;

  const total = values.reduce((a, b) => a + b, 0);
  if (total < 5) return 0; // Clamp to 0 when too few mentions to avoid noise

  const min = Math.min(...values);
  const max = Math.max(...values);
  const mean = total / values.length;

  return mean > 0 ? (max - min) / mean : 0;
}

/** Compute mean confidence across all encoders. */
function _computeMeanConfidence(events: RunEvent[], docId: string): number | null {
  const confidences: number[] = [];
  const docPrefix = `${docId}:_ner_`;

  for (const e of events) {
    if (!e.chunk_id?.startsWith(docPrefix)) continue;
    if (e.node_name !== "chunk_extracted" || e.status !== "completed") continue;

    const details = (e.details ?? {}) as {
      mentions?: Array<{ confidence?: number; conf?: number }>;
    };
    const mentions = details.mentions ?? [];

    for (const m of mentions) {
      const conf = m.confidence ?? m.conf;
      if (typeof conf === "number") {
        confidences.push(conf);
      }
    }
  }

  if (confidences.length === 0) return null;
  const mean = confidences.reduce((a, b) => a + b, 0) / confidences.length;
  return mean;
}

/** Detect zero-pipeline nodes: status="completed" but details={} or missing. */
function _computeZeroPipelineCount(events: RunEvent[], docId: string): number {
  const docPrefix = `${docId}:`;
  let count = 0;

  for (const e of events) {
    if (!e.chunk_id?.startsWith(docPrefix)) continue;
    if (e.status !== "completed") continue;

    const details = e.details ?? {};
    const isEmpty =
      Object.keys(details).length === 0 ||
      (typeof details === "object" &&
        Object.values(details).every((v) => v === null || v === undefined || v === ""));

    if (isEmpty) {
      count++;
    }
  }

  return count;
}

export function PathologyRollup({ events, docId }: Props) {
  const signals = useMemo<PathologySignal[]>(() => {
    const out: PathologySignal[] = [];

    // Signal 1: Consensus rejection ratio
    const consensusReject = _computeConsensusRejection(events, docId);
    out.push({
      id: "consensus-reject",
      name: "Consensus rejection",
      status:
        consensusReject > THRESHOLDS.consensusRejectHard
          ? "hard"
          : consensusReject > THRESHOLDS.consensusRejectSoft
            ? "soft"
            : "ok",
      value: (consensusReject * 100).toFixed(1) + "%",
      threshold: `soft >30%, hard >45%`,
      hint: "Fraction of consensus mentions that failed to reach encoder quorum.",
    });

    // Signal 2: Pack prune ratio
    const packPrune = _computePackPruneRatio(events, docId);
    out.push({
      id: "pack-prune",
      name: "Pack prune ratio",
      status:
        packPrune > THRESHOLDS.packPruneHard
          ? "hard"
          : packPrune > THRESHOLDS.packPruneSoft
            ? "soft"
            : "ok",
      value: (packPrune * 100).toFixed(1) + "%",
      threshold: `soft >50%, hard >75%`,
      hint: "Fraction of evidence windows pruned before SPO extraction.",
    });

    // Signal 3: Encoder disagreement
    const encoderCounts = _extractEncoderMentionCounts(events, docId);
    const encoderDisagreement = _computeEncoderDisagreement(encoderCounts);
    out.push({
      id: "encoder-disagreement",
      name: "Encoder disagreement",
      status:
        encoderDisagreement > THRESHOLDS.encoderDisagreementHard
          ? "hard"
          : encoderDisagreement > THRESHOLDS.encoderDisagreementSoft
            ? "soft"
            : "ok",
      value: encoderDisagreement.toFixed(2),
      threshold: `soft >0.3, hard >0.5`,
      hint: "Normalized variance in mention counts: (max - min) / mean. 0 = unanimous, >0.5 = severe disagreement.",
    });

    // Signal 4: Confidence skew
    const meanConf = _computeMeanConfidence(events, docId);
    out.push({
      id: "confidence-skew",
      name: "Confidence skew",
      status:
        meanConf === null || meanConf < 0.5
          ? "hard"
          : meanConf < THRESHOLDS.confidenceSkewSoft
            ? "soft"
            : "ok",
      value: meanConf !== null ? meanConf.toFixed(2) : "null",
      threshold: `soft <0.6, hard <0.5 or null`,
      hint: "Mean confidence across all encoder mentions. Low values suggest uncertain extractions.",
    });

    // Signal 5: Zero-pipeline
    const zeroPipelineCount = _computeZeroPipelineCount(events, docId);
    out.push({
      id: "zero-pipeline",
      name: "Zero-pipeline nodes",
      status: zeroPipelineCount > 0 ? "hard" : "ok",
      value: zeroPipelineCount.toString(),
      threshold: `binary: >0 is hard`,
      hint: "Number of completed nodes with empty details. Suggests incomplete processing or data loss.",
    });

    return out;
  }, [events, docId]);

  return (
    <div data-testid="pathology-rollup" className="mt-4 border-t border-white/10 pt-3">
      <div className="text-[10px] uppercase text-zinc-500 mb-2 tracking-wide font-semibold">
        Document Health
      </div>
      <div className="space-y-1.5">
        {signals.map((signal) => (
          <PathologyRow key={signal.id} signal={signal} />
        ))}
      </div>
    </div>
  );
}

function PathologyRow({ signal }: { signal: PathologySignal }) {
  const statusColor = {
    ok: "text-emerald-400",
    soft: "text-amber-400",
    hard: "text-rose-400",
  }[signal.status];

  const statusIcon = {
    ok: "✓",
    soft: "⚠",
    hard: "✗",
  }[signal.status];

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          data-testid={`pathology-row-${signal.id}`}
          data-status={signal.status}
          className="flex items-center justify-between gap-2 px-2 py-1.5 rounded bg-white/[0.02] hover:bg-white/5 transition-colors cursor-help text-[11px]"
        >
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <span className={`flex-shrink-0 font-bold ${statusColor}`}>{statusIcon}</span>
            <span className="text-zinc-300 truncate">{signal.name}</span>
          </div>
          <span className="text-zinc-500 text-right flex-shrink-0">{signal.value}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="left" className="max-w-xs">
        <div className="space-y-1">
          <div className="font-semibold">{signal.name}</div>
          <div className="text-[11px] text-zinc-300">{signal.hint}</div>
          <div className="text-[10px] text-zinc-500">Threshold: {signal.threshold}</div>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
