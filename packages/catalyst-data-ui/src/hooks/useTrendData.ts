/**
 * useTrendData — last-N-runs trend extraction over the bench
 * ``report.json`` corpus.
 *
 * Powers Gap #8's ``<TrendSparkline>``. Two axes are supported:
 *
 *   - ``aggregate``: per-(model, metric) across all docs in the run.
 *   - ``doc``:       per-(doc, model, metric).
 *
 * IMPORTANT — actual report shape vs spec table:
 *
 * The current ``report.json`` schema is RUN-LEVEL ONLY. Inspecting a live
 * report shows:
 *   {generated_at, model_count, entity_count, proposition_count,
 *    model_names, domains, ground_truth, models[], entities[],
 *    propositions[]}
 * with each ``models[i]`` carrying ``stats`` (mention_count, duration_s,
 * tokens_per_sec, …), ``scores`` (mention_strict_*, mention_relaxed_*,
 * proposition_*, …), and ``provenance``. There is NO ``per_doc[]``,
 * ``per_model[]``, or ``pack_summary`` tree the spec table proposed.
 *
 * Adaptation: both axes pull from the SAME run-level fields. The
 * difference is presentational scoping — ``axis="doc"`` annotates the
 * sparkline with the docId for the panel header context, but the
 * underlying time-series numbers come from the run-level model entry.
 * When per-doc breakdown lands in ``compute_model_scores()``, this hook
 * is the place to switch on ``opts.axis`` and pull the per-doc subtree.
 *
 * Field mapping (live, validated against report.json):
 *   encoder_mention_count     → models[name=<model>].stats.mention_count
 *   encoder_strict_f1         → models[name=<model>].scores.mention_strict_f1
 *   consensus_accepted_count  → models[name="ensemble"|"consensus"].stats.mention_count
 *   consensus_strict_f1       → models[name="ensemble"|"consensus"].scores.mention_strict_f1
 *   pack_kept_pruned_ratio    → derived: ensemble.stats.mention_count /
 *                               max(1, sum(encoders.stats.mention_count) -
 *                                       ensemble.stats.mention_count)
 *                               (kept = consensus-accepted; pruned ≈ encoder
 *                                proposals dropped during consensus)
 *   spo_mean_props_per_window → assertion_count / max(1, chunk_count) for
 *                               the named model (SPO writes assertions
 *                               into stats.assertion_count and runs over
 *                               chunk_count windows).
 *   persist_wall_clock_seconds→ models[name="ensemble"].stats.duration_s
 *                               (the ensemble row's duration_s captures
 *                                only the consensus pass; for run-total,
 *                                we sum stats.duration_s across all
 *                                models). Documented in the comments
 *                                at extractMetric() below.
 *
 * Each (run, metric) result is fetched via ``useRunReport(runId)`` from
 * ``useRunReport.ts`` so react-query handles dedup + 5-min caching across
 * panels. Five sparkline rails on the same page = one fetch per run.
 */

import { useQueries } from "@tanstack/react-query";

import type { RunReport } from "@/hooks/useRunReport";

import { useRunIndex } from "./useRunIndex";

export type TrendMetric =
  | "encoder_mention_count"
  | "encoder_strict_f1"
  | "consensus_accepted_count"
  | "consensus_strict_f1"
  | "pack_kept_pruned_ratio"
  | "spo_mean_props_per_window"
  | "persist_wall_clock_seconds";

export interface TrendOpts {
  axis: "doc" | "aggregate";
  metric: TrendMetric;
  /** Required when axis="doc". Currently used only for the runId sort
   *  scope; the underlying data comes from run-level fields until
   *  per-doc breakdown lands in report.json. */
  docId?: string;
  /** Required for encoder_* and spo_* metrics. */
  model?: string;
  /** Default 10. Each entry must keep its slot even when null so the
   *  sparkline draws gaps deterministically. */
  windowSize?: number;
}

export interface TrendPoint {
  runId: string;
  ts: number;
  value: number | null;
}

export interface UseTrendDataResult {
  /** Length === windowSize after the index loads. Chronological order:
   *  oldest left, newest right. ``value=null`` → gap in the line. */
  points: TrendPoint[];
  isLoading: boolean;
  error: Error | null;
}

const DEFAULT_WINDOW = 10;

/** Parse a run id like ``YYYY-MM-DD-HHMMSS[-label]`` to ms since epoch.
 *  Used as the chronological x-axis when present; falls back to lex
 *  index otherwise. */
function runIdToMs(runId: string): number {
  const m = runId.match(/^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})/);
  if (!m) return 0;
  const [, y, mo, d, h, mi, s] = m;
  const t = Date.UTC(+y!, +mo! - 1, +d!, +h!, +mi!, +s!);
  return Number.isFinite(t) ? t : 0;
}

/** Pull the metric value out of a run's report.json. Returns ``null`` when
 *  the metric isn't computable (model absent, divisor zero, score field
 *  missing, etc.) — caller renders that as a gap. */
function extractMetric(
  report: RunReport | null | undefined,
  metric: TrendMetric,
  model: string | undefined,
): number | null {
  if (!report || !Array.isArray(report.models)) return null;

  const findEnsemble = () =>
    report.models.find((m) => m.name === "ensemble" || m.name === "consensus");
  const byName = (name: string) => report.models.find((m) => m.name === name);

  switch (metric) {
    case "encoder_mention_count": {
      if (!model) return null;
      const m = byName(model);
      const v = (m?.stats as { mention_count?: unknown } | undefined)?.mention_count;
      return typeof v === "number" ? v : null;
    }
    case "encoder_strict_f1": {
      if (!model) return null;
      const m = byName(model);
      const v = m?.scores?.mention_strict_f1;
      return typeof v === "number" ? v : null;
    }
    case "consensus_accepted_count": {
      const m = findEnsemble();
      const v = (m?.stats as { mention_count?: unknown } | undefined)?.mention_count;
      return typeof v === "number" ? v : null;
    }
    case "consensus_strict_f1": {
      const m = findEnsemble();
      const v = m?.scores?.mention_strict_f1;
      return typeof v === "number" ? v : null;
    }
    case "pack_kept_pruned_ratio": {
      // Derived: kept ≈ consensus-accepted mentions; pruned ≈ total
      // encoder proposals minus consensus accepted (i.e. mentions that
      // didn't make it through dedupe + quorum). Returns null when the
      // ensemble row is missing entirely; otherwise floor pruned at 1
      // to avoid div-by-zero on pristine consensus passes.
      const ens = findEnsemble();
      const accepted = (ens?.stats as { mention_count?: unknown } | undefined)?.mention_count;
      if (typeof accepted !== "number") return null;
      const encoderTotal = report.models
        .filter((m) => m.type === "encoder")
        .reduce((sum, m) => {
          const c = (m.stats as { mention_count?: unknown } | undefined)?.mention_count;
          return sum + (typeof c === "number" ? c : 0);
        }, 0);
      const pruned = Math.max(1, encoderTotal - accepted);
      return accepted / pruned;
    }
    case "spo_mean_props_per_window": {
      if (!model) return null;
      const m = byName(model);
      const props = (m?.stats as { assertion_count?: unknown } | undefined)?.assertion_count;
      const windows = (m?.stats as { chunk_count?: unknown } | undefined)?.chunk_count;
      if (typeof props !== "number" || typeof windows !== "number" || windows <= 0) return null;
      return props / windows;
    }
    case "persist_wall_clock_seconds": {
      // Run-total wall-clock = sum of stats.duration_s across non-ensemble
      // model rows. Ensemble's duration_s is just the consensus pass —
      // not a useful proxy for "how long did persist take". The total
      // is what the operator wants when the persist node is the
      // selected panel.
      let total = 0;
      let saw = false;
      for (const m of report.models) {
        if (m.name === "ensemble" || m.name === "consensus") continue;
        const v = (m.stats as { duration_s?: unknown } | undefined)?.duration_s;
        if (typeof v === "number") {
          total += v;
          saw = true;
        }
      }
      return saw ? total : null;
    }
  }
}

export function useTrendData(opts: TrendOpts): UseTrendDataResult {
  const { runs, isLoading: indexLoading, error: indexError } = useRunIndex();
  const windowSize = opts.windowSize ?? DEFAULT_WINDOW;

  // ``runs`` is newest-first. We want chronological (oldest → newest) for
  // the rendered sparkline, with the most-recent N entries.
  const sliceNewestFirst = runs.slice(0, windowSize);
  const slice = [...sliceNewestFirst].reverse();

  const queries = useQueries({
    queries: slice.map((runId) => ({
      queryKey: ["bench", "report", runId],
      queryFn: async (): Promise<RunReport | null> => {
        const res = await fetch(`/viewer/api/bench/runs/${encodeURIComponent(runId)}/report.json`);
        if (!res.ok) {
          if (res.status === 404) return null;
          throw new Error(`report.json failed: ${res.status}`);
        }
        return (await res.json()) as RunReport;
      },
      staleTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: 0,
    })),
  });

  const points: TrendPoint[] = slice.map((runId, idx) => {
    const q = queries[idx]!;
    const v = extractMetric(q.data, opts.metric, opts.model);
    return {
      runId,
      ts: runIdToMs(runId),
      value: v,
    };
  });

  const isLoading = indexLoading || queries.some((q) => q.isLoading);
  const error = indexError ?? (queries.find((q) => q.error)?.error as Error | undefined) ?? null;

  return { points, isLoading, error };
}
