/**
 * NerEncoderDetail — right-pane panel for a single encoder node in the
 * StateInspector V2 graph. Shows that encoder's mentions for the
 * selected doc, plus duration / status from `ner_encoder_completed`.
 */

import { useMemo } from "react";
import type { RunEvent } from "@/types/benchmark";
import { useRunReport, useActiveGroundTruth } from "@/hooks/useRunReport";
import { useTrendData } from "@/hooks/useTrendData";

import { MentionTable, type Mention } from "./MentionTable";
import { F1Strip, type F1Scores } from "./F1Strip";
import { ConfidenceHistogram } from "./ConfidenceHistogram";
import { TrendSparkline } from "../TrendSparkline";
import { DeepLinkButton } from "./DeepLinkButton";

interface Props {
  events: RunEvent[];
  docId: string;
  encoder: string;
  /** Active run id — pinned via the RunPicker, or the latest run when no
   *  pin is set. ``null`` when no runs exist (initial-load), in which
   *  case the F1 strip skips render. */
  runId: string | null;
  /** Gap #8 — selection-preserving run jump. Defaults to no-op so other
   *  call sites (storybook, isolated tests) don't have to wire it. */
  onJumpRun?: (runId: string) => void;
}

interface MentionLite {
  text: string;
  mention_type?: string;
  type?: string;
  span_start?: number | null;
  span_end?: number | null;
  confidence?: number;
}

export function NerEncoderDetail({ events, docId, encoder, runId, onJumpRun }: Props) {
  const chunkId = `${docId}:_ner_${encoder}`;
  const { data: report } = useRunReport(runId);
  const { data: gtMentions, isSuccess: gtLoaded } = useActiveGroundTruth();
  // Gap #8 — last-10-runs trend for this (encoder, doc). Use mention
  // count as the "always-renders" smoke metric (counts don't depend on
  // GT). The header sparkline highlights the current run + click-to-
  // jump preserves doc + node selection.
  const { points: trendPoints } = useTrendData({
    axis: "doc",
    metric: "encoder_mention_count",
    docId,
    model: encoder,
  });

  // Scope GT to this doc — the active GT may cover many docs, but the
  // histogram only cares about rows that could possibly match this
  // encoder's mentions. ``null`` here means "no GT active for this view"
  // (so the component renders the single-tone fallback); ``[]`` means
  // "GT loaded, but no rows scoped to this doc" (still single-tone, but
  // we've at least tried to match).
  const scopedGtList = useMemo(() => {
    if (!gtLoaded || !gtMentions) return null;
    const docPrefix = `${docId}:`;
    return gtMentions.filter(
      (g) => g.doc_id === docId || (g.chunk_id ? g.chunk_id.startsWith(docPrefix) : false),
    );
  }, [gtLoaded, gtMentions, docId]);

  // Look up this encoder's strict scores from the per-run report. Returns
  // null when the encoder isn't in the report (e.g. it errored out and
  // never produced mentions, or the run has no GT).
  const f1Scores = useMemo<F1Scores | null>(() => {
    if (!report || !report.ground_truth?.available) return null;
    const entry = report.models.find((m) => m.name === encoder);
    const s = entry?.scores;
    if (!s || s.mention_strict_f1 === undefined) return null;
    return {
      precision: s.mention_strict_precision ?? 0,
      recall: s.mention_strict_recall ?? 0,
      strict_f1: s.mention_strict_f1 ?? 0,
      partial_f1: s.mention_relaxed_f1,
    };
  }, [report, encoder]);

  // Encoder strip leans amber when this encoder is lagging the leader by
  // more than 0.02. Pull the leader from the encoder-typed entries
  // (encoder type is what we actually want to compare against — LLMs
  // and ensembles are different beasts).
  const leadingThresholdMet = useMemo(() => {
    if (!report || !f1Scores) return undefined;
    const encoderEntries = report.models.filter((m) => m.type === "encoder");
    if (encoderEntries.length === 0) return undefined;
    const best = Math.max(...encoderEntries.map((m) => m.scores?.mention_strict_f1 ?? 0));
    return f1Scores.strict_f1 >= best - 0.02;
  }, [report, f1Scores]);
  const summary = useMemo(() => {
    const ext = events.find((e) => e.node_name === "chunk_extracted" && e.chunk_id === chunkId);
    const completed = events.find(
      (e) => e.node_name === "ner_encoder_completed" && e.chunk_id === chunkId,
    );
    const mentions: MentionLite[] = (ext?.details?.mentions as MentionLite[]) ?? [];
    const d = (completed?.details ?? {}) as Record<string, unknown>;
    return {
      mentions,
      duration: typeof d.duration_s === "number" ? (d.duration_s as number) : null,
      status: completed?.status ?? "unknown",
      error:
        (d.error as { type?: string; message?: string } | undefined) ??
        (ext?.details?.error as { type?: string; message?: string } | undefined),
    };
  }, [events, chunkId]);

  const typeTally = useMemo(() => {
    const m = new Map<string, number>();
    for (const x of summary.mentions) {
      const t = x.mention_type ?? x.type ?? "—";
      m.set(t, (m.get(t) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [summary.mentions]);

  return (
    <div data-testid="ner-encoder-detail" className="p-3 font-mono text-[11px] space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-zinc-300 text-[12px]">{encoder}</div>
          <div className="text-zinc-500 text-[10px] mt-0.5">{docId}</div>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <DeepLinkButton testidPrefix="encoder" panelName="encoder" />
          <TrendSparkline
            points={trendPoints}
            metric="encoder_mention_count"
            currentRunId={runId}
            onSelectRun={(id) => onJumpRun?.(id)}
            trend="up-good"
          />
        </div>
      </div>
      <div className="flex flex-wrap gap-3 text-[10px] text-zinc-400">
        <span>
          status:{" "}
          <span
            data-testid="ner-encoder-status"
            className={
              summary.status === "ok"
                ? "text-emerald-300"
                : summary.status === "error"
                  ? "text-red-300"
                  : "text-zinc-500"
            }
          >
            {summary.status}
          </span>
        </span>
        {summary.duration != null && (
          <span>
            duration: <span className="text-zinc-200">{summary.duration.toFixed(2)}s</span>
          </span>
        )}
        <span>
          mentions:{" "}
          <span data-testid="ner-encoder-mention-count" className="text-zinc-200">
            {summary.mentions.length}
          </span>
        </span>
      </div>
      <F1Strip scores={f1Scores} leadingThresholdMet={leadingThresholdMet} />
      {summary.error?.message && (
        <div
          data-testid="ner-encoder-error"
          className="bg-red-500/10 border border-red-500/40 rounded px-2 py-1 text-red-300 text-[10px]"
        >
          {summary.error.type ?? "error"}: {summary.error.message}
        </div>
      )}
      {typeTally.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {typeTally.map(([t, n]) => (
            <span
              key={t}
              data-testid="ner-encoder-type-pill"
              className="px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-200 text-[9px]"
            >
              {t}×{n}
            </span>
          ))}
        </div>
      )}
      {summary.mentions.length > 0 && (
        <ConfidenceHistogram
          mentions={summary.mentions.map((m) => ({
            confidence: m.confidence ?? null,
            text: m.text,
            mention_type: m.mention_type ?? m.type,
            span_start: m.span_start ?? null,
            span_end: m.span_end ?? null,
            doc_id: docId,
            chunk_id: chunkId,
          }))}
          gtList={scopedGtList}
          encoderName={encoder}
        />
      )}
      <div className="space-y-1">
        <div className="text-zinc-500 uppercase text-[9px] tracking-wide">mentions</div>
        <MentionTable
          rows={summary.mentions.map<Mention>((m) => ({
            text: m.text,
            type: m.mention_type ?? m.type ?? undefined,
            confidence: m.confidence ?? undefined,
            variant: "muted",
          }))}
          columns={["text", "type", "conf"]}
          emptyMessage="no mentions extracted"
          rowTestId="ner-encoder-mention-row"
        />
      </div>
    </div>
  );
}
