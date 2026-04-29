import { useEffect, useState } from "react";
import type { BenchmarkReport as BenchmarkReportType } from "@/types/benchmark";
import {
  StatCard,
  PerformanceChart,
  ScoreBarChart,
  METRIC_TOOLTIPS,
} from "@/components/benchmark/shared";
import { ModelStatsTable } from "@/components/benchmark/ModelStatsTable";
import { ScoresTable } from "@/components/benchmark/ScoresTable";
import { EntityMatrix } from "@/components/benchmark/EntityMatrix";
import { PropositionMatrix } from "@/components/benchmark/PropositionMatrix";
import { PipelineTable } from "@/components/benchmark/PipelineTable";
import { AuditViewer } from "@/components/benchmark/AuditViewer";

// ── Scores Tab Content ────────────────────────────────────────────────
function ScoresTab({ report }: { report: BenchmarkReportType }) {
  const gt = report.ground_truth;

  if (!gt || !gt.available) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <div className="text-zinc-400 text-sm mb-2">No ground truth available.</div>
        <div className="text-zinc-500 text-xs mb-4">
          Generate ground truth to see F1 scores, precision, and recall for each model.
        </div>
        <pre className="text-xs text-zinc-400 bg-surface-1 border border-white/5 rounded p-3">
          pytest tests/test_extraction_benchmark.py -k generate_ground_truth -v -s
        </pre>
      </div>
    );
  }

  const scored = report.models.filter((m) => m.scores != null);
  if (scored.length === 0) {
    return (
      <div className="text-zinc-500 text-sm py-4">
        Ground truth exists but no models have been scored yet.
      </div>
    );
  }

  // Best scores across all models
  const bestF1 = scored.reduce((best, m) =>
    m.scores!.mention_strict_f1 > best.scores!.mention_strict_f1 ? m : best,
  );
  const bestPrecision = scored.reduce((best, m) =>
    m.scores!.mention_strict_precision > best.scores!.mention_strict_precision ? m : best,
  );
  const bestRecall = scored.reduce((best, m) =>
    m.scores!.mention_strict_recall > best.scores!.mention_strict_recall ? m : best,
  );
  const bestPropF1 = scored.reduce((best, m) =>
    m.scores!.proposition_relaxed_f1 > best.scores!.proposition_relaxed_f1 ? m : best,
  );

  return (
    <div className="space-y-6">
      {/* Ground truth info */}
      <div className="bg-surface-1 border border-white/5 rounded-lg p-3 flex items-center gap-4 text-xs font-mono">
        <span className="text-zinc-500">Ground Truth:</span>
        <span className="text-zinc-300">{gt.reference_model}</span>
        <span className={gt.manually_reviewed ? "text-emerald-400" : "text-amber-400"}>
          {gt.manually_reviewed ? "Reviewed" : "Unreviewed"}
        </span>
        <span className="text-zinc-500">
          {gt.mention_count} mentions, {gt.proposition_count} propositions
        </span>
      </div>

      {/* Best score cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="Best Strict F1"
          value={`${(bestF1.scores!.mention_strict_f1 * 100).toFixed(1)}%`}
          sub={bestF1.name}
          tooltip={METRIC_TOOLTIPS.strict_f1}
        />
        <StatCard
          label="Best Precision"
          value={`${(bestPrecision.scores!.mention_strict_precision * 100).toFixed(1)}%`}
          sub={bestPrecision.name}
          tooltip={METRIC_TOOLTIPS.precision}
        />
        <StatCard
          label="Best Recall"
          value={`${(bestRecall.scores!.mention_strict_recall * 100).toFixed(1)}%`}
          sub={bestRecall.name}
          tooltip={METRIC_TOOLTIPS.recall}
        />
        <StatCard
          label="Best Prop F1"
          value={`${(bestPropF1.scores!.proposition_relaxed_f1 * 100).toFixed(1)}%`}
          sub={bestPropF1.name}
          tooltip={METRIC_TOOLTIPS.proposition_relaxed_f1}
        />
      </div>

      {/* F1 comparison bar charts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ScoreBarChart
          models={report.models}
          metricKey="mention_strict_f1"
          label="Mention Strict F1"
          tooltip={METRIC_TOOLTIPS.strict_f1}
        />
        <ScoreBarChart
          models={report.models}
          metricKey="mention_relaxed_f1"
          label="Mention Relaxed F1"
          tooltip={METRIC_TOOLTIPS.relaxed_f1}
        />
        <ScoreBarChart
          models={report.models}
          metricKey="proposition_strict_f1"
          label="Proposition Strict F1"
          tooltip={METRIC_TOOLTIPS.proposition_strict_f1}
        />
        <ScoreBarChart
          models={report.models}
          metricKey="proposition_relaxed_f1"
          label="Proposition Relaxed F1"
          tooltip={METRIC_TOOLTIPS.proposition_relaxed_f1}
        />
      </div>

      {/* Efficiency metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ScoreBarChart
          models={report.models}
          metricKey="quality_speed_ratio"
          label="Quality/Speed (F1 per second)"
          isPercentage={false}
          format={(v) => v.toFixed(3)}
          tooltip={METRIC_TOOLTIPS.quality_speed_ratio}
        />
        <ScoreBarChart
          models={report.models}
          metricKey="per_chunk_latency"
          label="Per-Chunk Latency (lower is better)"
          isPercentage={false}
          invertSort={true}
          format={(v) => `${v.toFixed(2)}s`}
          tooltip={METRIC_TOOLTIPS.per_chunk_latency}
        />
      </div>
      {scored.some((m) => m.scores!.hallucination_rate < 1.0) && (
        <ScoreBarChart
          models={report.models}
          metricKey="hallucination_rate"
          label="Hallucination Rate (lower is better)"
          invertSort={true}
          tooltip={METRIC_TOOLTIPS.hallucination_rate}
        />
      )}
      {scored.every((m) => m.scores!.hallucination_rate >= 1.0) && (
        <p className="text-[10px] text-zinc-600">
          Hallucination rate hidden — source_text not provided to scoring (span_accuracy = 0 for all
          models).
        </p>
      )}

      {/* Full precision/recall table */}
      <div>
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-3">
          Precision / Recall / F1 Breakdown
        </h3>
        <ScoresTable models={report.models} />
      </div>
    </div>
  );
}

// ── Report sources ────────────────────────────────────────────────────

interface ReportSource {
  label: string;
  url: string;
}

const REPORT_SOURCES: ReportSource[] = [
  { label: "Latest Run", url: "/viewer/runs/latest/benchmark-report.json" },
  { label: "Latest", url: "/viewer/benchmark-report.json" },
  { label: "v2 (exgraph)", url: "/viewer/compare-v2/media-ingest/benchmark-report.json" },
  { label: "v1 (legacy)", url: "/viewer/compare-v1/media-ingest/benchmark-report.json" },
];

// ── Main Page ──────────────────────────────────────────────────────────
export default function BenchmarkReport() {
  const [report, setReport] = useState<BenchmarkReportType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reportSource, setReportSource] = useState(REPORT_SOURCES[0]!.url);
  const [availableSources, setAvailableSources] = useState<ReportSource[]>([]);
  const [activeTab, setActiveTab] = useState<
    "overview" | "scores" | "entities" | "propositions" | "pipeline" | "audit"
  >("overview");

  // Probe which report sources exist
  useEffect(() => {
    Promise.all(
      REPORT_SOURCES.map(async (src) => {
        try {
          const res = await fetch(src.url, { method: "HEAD" });
          return res.ok ? src : null;
        } catch {
          return null;
        }
      }),
    ).then((results) => {
      const available = results.filter((r): r is ReportSource => r !== null);
      setAvailableSources(available);
      // Default to first available
      if (available.length > 0 && !available.find((s) => s.url === reportSource)) {
        setReportSource(available[0]!.url);
      }
    });
  }, []);

  // Load the selected report
  useEffect(() => {
    setReport(null);
    setError(null);
    fetch(reportSource)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}: ${r.statusText}`);
        return r.json();
      })
      .then(setReport)
      .catch((e) =>
        setError(`Could not load benchmark report: ${e.message}. Run the benchmark first.`),
      );
  }, [reportSource]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="bg-surface-1 border border-white/5 rounded-lg p-8 max-w-lg text-center">
          <h2 className="text-lg font-mono text-zinc-200 mb-2">No Benchmark Report</h2>
          <p className="text-sm text-zinc-500 mb-4">{error}</p>
          <pre className="text-xs text-zinc-400 bg-surface-0 rounded p-3 text-left">
            PYTHONPATH=. python tests/benchmark_harness.py --full
          </pre>
        </div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-zinc-500 font-mono text-sm">Loading report...</div>
      </div>
    );
  }

  const hasScores = report.models.some((m) => m.scores != null);
  const tabs = [
    { key: "overview" as const, label: "Overview" },
    {
      key: "scores" as const,
      label: hasScores ? `Scores (${report.models.filter((m) => m.scores).length})` : "Scores",
    },
    { key: "entities" as const, label: `Entities (${report.entity_count})` },
    {
      key: "propositions" as const,
      label: `Propositions (${report.proposition_count})`,
    },
    { key: "pipeline" as const, label: "Pipeline" },
    { key: "audit" as const, label: "Audit" },
  ];

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1400px] mx-auto p-6 space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-mono text-zinc-100">Extraction Benchmark Report</h1>
            <p className="text-xs text-zinc-500 font-mono mt-1">
              Generated {new Date(report.generated_at).toLocaleString()} — {report.model_count}{" "}
              models, {report.entity_count} unique entities, {report.proposition_count} propositions
            </p>
          </div>
          {availableSources.length > 1 && (
            <select
              value={reportSource}
              onChange={(e) => setReportSource(e.target.value)}
              className="bg-surface-1 border border-white/10 rounded px-2 py-1 text-xs font-mono text-zinc-200"
            >
              {availableSources.map((src) => (
                <option key={src.url} value={src.url}>
                  {src.label}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Stat Cards */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatCard label="Models" value={report.model_count} tooltip={METRIC_TOOLTIPS.models} />
          <StatCard
            label="Unique Entities"
            value={report.entity_count}
            tooltip={METRIC_TOOLTIPS.unique_entities}
          />
          <StatCard
            label="Propositions"
            value={report.proposition_count}
            tooltip={METRIC_TOOLTIPS.propositions}
          />
          <StatCard
            label="Domains"
            value={Object.keys(report.domains || {}).length}
            sub={Object.entries(report.domains || {})
              .map(([d, n]) => `${d}: ${n}`)
              .join(", ")}
            tooltip={METRIC_TOOLTIPS.domains}
          />
          <StatCard
            label="Fastest"
            value={
              [...report.models].sort((a, b) => a.stats.duration_s - b.stats.duration_s)[0]?.name ||
              "—"
            }
            sub={`${[...report.models].sort((a, b) => a.stats.duration_s - b.stats.duration_s)[0]?.stats.duration_s.toFixed(1)}s`}
            tooltip={METRIC_TOOLTIPS.fastest}
          />
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-white/5 pb-0">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`px-4 py-2 text-xs font-mono rounded-t transition-colors ${
                activeTab === t.key
                  ? "bg-surface-1 text-zinc-100 border border-white/5 border-b-0"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div className="bg-surface-0 border border-white/5 rounded-lg p-4">
          {activeTab === "overview" && (
            <div className="space-y-6">
              {/* Performance charts */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <PerformanceChart
                  models={report.models}
                  metric="mention_count"
                  label="Mentions Extracted"
                  tooltip={METRIC_TOOLTIPS.mentions}
                />
                <PerformanceChart
                  models={report.models}
                  metric="assertion_count"
                  label="Assertions Extracted"
                  tooltip={METRIC_TOOLTIPS.assertions}
                />
                <PerformanceChart
                  models={report.models}
                  metric="tokens_per_sec"
                  label="Speed (tok/s)"
                  format={(v) => `${v.toFixed(0)}`}
                  tooltip={METRIC_TOOLTIPS.tokens_per_sec}
                />
                <PerformanceChart
                  models={report.models}
                  metric="duration_s"
                  label="Total Time"
                  format={(v) => `${v.toFixed(1)}s`}
                  tooltip={METRIC_TOOLTIPS.duration}
                />
              </div>

              {/* Model stats table */}
              <div>
                <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-3">
                  Models by Type
                </h3>
                <ModelStatsTable models={report.models} />
              </div>
            </div>
          )}

          {activeTab === "scores" && <ScoresTab report={report} />}

          {activeTab === "entities" && (
            <EntityMatrix entities={report.entities} modelNames={report.model_names} />
          )}

          {activeTab === "propositions" && (
            <PropositionMatrix propositions={report.propositions} modelNames={report.model_names} />
          )}

          {activeTab === "pipeline" && (
            <div className="space-y-4">
              <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider">
                LangGraph Pipeline — MCP Validation Gates + Repair Flows
              </h3>
              <PipelineTable models={report.models} />
            </div>
          )}

          {activeTab === "audit" && <AuditViewer modelNames={report.model_names} />}
        </div>
      </div>
    </div>
  );
}
