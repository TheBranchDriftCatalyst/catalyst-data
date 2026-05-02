import { useEffect, useMemo, useState } from "react";
import type { BenchmarkReport as BenchmarkReportType, EntityRow } from "@/types/benchmark";
import {
  StatCard,
  PerformanceChart,
  ScoreBarChart,
  ProvenanceBarChart,
  METRIC_TOOLTIPS,
} from "@/components/benchmark/shared";
import type { GroupByDimension } from "@/components/benchmark/shared";
import { ModelStatsTable } from "@/components/benchmark/ModelStatsTable";
import { ScoresTable } from "@/components/benchmark/ScoresTable";
import { EntityMatrix } from "@/components/benchmark/EntityMatrix";
import { EntityJsonPanel } from "@/components/benchmark/EntityJsonPanel";
import { PropositionMatrix } from "@/components/benchmark/PropositionMatrix";
import { PipelineTable } from "@/components/benchmark/PipelineTable";
import { AuditViewer } from "@/components/benchmark/AuditViewer";
import { LiveGantt } from "@/components/benchmark/LiveGantt";
import { GroundTruthPanel } from "@/components/benchmark/GroundTruthPanel";
import { TableControls } from "@/components/benchmark/TableControls";
import { GTSelector } from "@/components/benchmark/GTSelector";
import { DomainBreakdown } from "@/components/benchmark/DomainBreakdown";

// ── Scores Tab Content ────────────────────────────────────────────────
function ScoresTab({
  report,
  visibleModels,
  groupBy,
}: {
  report: BenchmarkReportType;
  visibleModels: import("@/types/benchmark").ModelResult[];
  groupBy: GroupByDimension;
}) {
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

  const scored = visibleModels.filter((m) => m.scores != null);
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
      {/* Ground truth info (selector is in global controls bar) */}
      <div className="bg-surface-1 border border-white/5 rounded-lg p-3 flex items-center gap-4 text-xs font-mono">
        <span className="text-zinc-500">Scoring against:</span>
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
          models={visibleModels}
          metricKey="mention_strict_f1"
          label="Mention Strict F1"
          tooltip={METRIC_TOOLTIPS.strict_f1}
        />
        <ScoreBarChart
          models={visibleModels}
          metricKey="mention_relaxed_f1"
          label="Mention Relaxed F1"
          tooltip={METRIC_TOOLTIPS.relaxed_f1}
        />
        <ScoreBarChart
          models={visibleModels}
          metricKey="proposition_strict_f1"
          label="Proposition Strict F1"
          tooltip={METRIC_TOOLTIPS.proposition_strict_f1}
        />
        <ScoreBarChart
          models={visibleModels}
          metricKey="proposition_relaxed_f1"
          label="Proposition Relaxed F1"
          tooltip={METRIC_TOOLTIPS.proposition_relaxed_f1}
        />
      </div>

      {/* Efficiency metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ScoreBarChart
          models={visibleModels}
          metricKey="quality_speed_ratio"
          label="Quality/Speed (F1 per second)"
          isPercentage={false}
          format={(v) => v.toFixed(3)}
          tooltip={METRIC_TOOLTIPS.quality_speed_ratio}
        />
        <ScoreBarChart
          models={visibleModels}
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
          models={visibleModels}
          metricKey="hallucination_rate"
          label="Hallucination Rate (lower is better)"
          invertSort={true}
          tooltip={METRIC_TOOLTIPS.hallucination_rate}
        />
      )}
      {scored.every((m) => m.scores!.hallucination_rate >= 1.0) && (
        <p className="text-[11px] text-zinc-500">
          Hallucination rate hidden — source_text not provided to scoring (span_accuracy = 0 for all
          models).
        </p>
      )}

      {/* Provenance completeness */}
      {visibleModels.some((m) => m.provenance) && (
        <div>
          <h3 className="text-xs font-mono text-zinc-300 uppercase tracking-wider mb-3">
            Provenance Chain Completeness
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <ProvenanceBarChart
              models={visibleModels}
              metricKey="overall"
              label="Overall Provenance"
              tooltip={METRIC_TOOLTIPS.provenance_overall}
            />
            <ProvenanceBarChart
              models={visibleModels}
              metricKey="has_span"
              label="Span Positions"
              tooltip={METRIC_TOOLTIPS.provenance_has_span}
            />
            <ProvenanceBarChart
              models={visibleModels}
              metricKey="assertion_linked_subject"
              label="Assertion → Subject Linked"
              tooltip={METRIC_TOOLTIPS.provenance_linked_subject}
            />
            <ProvenanceBarChart
              models={visibleModels}
              metricKey="assertion_linked_object"
              label="Assertion → Object Linked"
              tooltip={METRIC_TOOLTIPS.provenance_linked_object}
            />
          </div>
        </div>
      )}

      {/* Full precision/recall table */}
      <div>
        <h3 className="text-xs font-mono text-zinc-300 uppercase tracking-wider mb-3">
          Precision / Recall / F1 Breakdown
        </h3>
        <ScoresTable models={visibleModels} groupBy={groupBy} />
      </div>
    </div>
  );
}

// ── Report sources ────────────────────────────────────────────────────

interface ReportSource {
  label: string;
  url: string;
}

// Report sources are now hydrated dynamically from /viewer/api/bench/runs
// at mount time — see useEffect below. The "Latest" entry is the top-level
// report S3 object copied at run end; per-run entries are added as they're
// discovered.
const REPORT_SOURCES: ReportSource[] = [{ label: "Latest", url: "/viewer/api/bench/report.json" }];

// ── Main Page ──────────────────────────────────────────────────────────
export default function BenchmarkReport() {
  const [report, setReport] = useState<BenchmarkReportType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reportSource, setReportSource] = useState(REPORT_SOURCES[0]!.url);
  const [availableSources, setAvailableSources] = useState<ReportSource[]>([]);
  const [activeTab, _setActiveTab] = useState<
    | "overview"
    | "scores"
    | "entities"
    | "propositions"
    | "domains"
    | "pipeline"
    | "audit"
    | "ground-truth"
  >("overview");
  // Wrapper that also clears the entity drawer when leaving the entities tab,
  // so reopening the tab doesn't unexpectedly resurrect a stale selection.
  const setActiveTab = (t: typeof activeTab) => {
    if (t !== "entities") setSelectedEntity(null);
    _setActiveTab(t);
  };
  const [groupBy, setGroupBy] = useState<GroupByDimension>("type");
  const [hiddenModels, setHiddenModels] = useState<Set<string>>(new Set());
  const [selectedGT, setSelectedGT] = useState("active");
  const [selectedEntity, setSelectedEntity] = useState<EntityRow | null>(null);

  // Hydrate the source dropdown from the bench API: top-level "Latest" plus
  // one entry per archived run. The bench routes return 404 cleanly when no
  // report exists, so a HEAD probe is enough to filter out empty entries.
  useEffect(() => {
    (async () => {
      const sources: ReportSource[] = [...REPORT_SOURCES];
      try {
        const r = await fetch("/viewer/api/bench/runs");
        if (r.ok) {
          const body = (await r.json()) as { runs: string[] };
          for (const id of body.runs) {
            sources.push({
              label: `Run ${id}`,
              url: `/viewer/api/bench/runs/${encodeURIComponent(id)}/report.json`,
            });
          }
        }
      } catch {
        // bench API down — show only the static "Latest" entry below.
      }
      const checked = await Promise.all(
        sources.map(async (src) => {
          try {
            const res = await fetch(src.url, { method: "HEAD" });
            return res.ok ? src : null;
          } catch {
            return null;
          }
        }),
      );
      const available = checked.filter((r): r is ReportSource => r !== null);
      setAvailableSources(available);
      if (available.length > 0 && !available.find((s) => s.url === reportSource)) {
        setReportSource(available[0]!.url);
      }
    })();
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

  const visibleModels = useMemo(
    () => (report?.models ?? []).filter((m) => !hiddenModels.has(m.name)),
    [report?.models, hiddenModels],
  );

  if (error) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <LiveGantt />
        <div className="flex items-center justify-center">
          <div className="bg-surface-1 border border-white/5 rounded-lg p-8 max-w-lg text-center">
            <h2 className="text-lg font-mono text-zinc-200 mb-2">No Benchmark Report</h2>
            <p className="text-sm text-zinc-500 mb-4">{error}</p>
            <pre className="text-xs text-zinc-400 bg-surface-0 rounded p-3 text-left">
              PYTHONPATH=. python tests/benchmark_harness.py --full
            </pre>
            <p className="text-[11px] text-zinc-600 mt-3">
              The live timeline above streams from the run-bus while a benchmark is in flight — the
              static report appears once the first model completes.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <LiveGantt />
        <div className="flex items-center justify-center">
          <div className="text-zinc-500 font-mono text-sm">Loading report...</div>
        </div>
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
    {
      key: "domains" as const,
      label: `Domains (${Object.keys(report.domains || {}).length})`,
    },
    { key: "pipeline" as const, label: "Pipeline" },
    { key: "audit" as const, label: "Audit" },
    { key: "ground-truth" as const, label: "Ground Truth" },
  ];

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1400px] mx-auto p-6 space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-xl font-mono text-zinc-100">Extraction Benchmark Report</h1>
          <p className="text-xs text-zinc-500 font-mono mt-1">
            Generated {new Date(report.generated_at).toLocaleString()} — {report.model_count}{" "}
            models, {report.entity_count} unique entities, {report.proposition_count} propositions
          </p>
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

        {/* Global Controls */}
        <div className="flex items-center gap-4 flex-wrap bg-surface-1 border border-white/5 rounded-lg px-4 py-2">
          {availableSources.length > 1 && (
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-zinc-500 font-mono uppercase">Report</span>
              <select
                value={reportSource}
                onChange={(e) => setReportSource(e.target.value)}
                aria-label="Select report version"
                className="bg-surface-0 border border-white/10 rounded px-2 py-0.5 text-xs font-mono text-zinc-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
              >
                {availableSources.map((src) => (
                  <option key={src.url} value={src.url}>
                    {src.label}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-zinc-500 font-mono uppercase">Ground Truth</span>
            <GTSelector selected={selectedGT} onChange={setSelectedGT} />
          </div>
          <TableControls
            models={report.models}
            groupBy={groupBy}
            onGroupByChange={setGroupBy}
            hiddenModels={hiddenModels}
            onHiddenModelsChange={setHiddenModels}
          />
        </div>

        {/* Tabs */}
        <div
          className="flex gap-1 border-b border-white/5 pb-0"
          role="tablist"
          aria-label="Benchmark report sections"
        >
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              role="tab"
              aria-selected={activeTab === t.key}
              aria-controls={`tabpanel-${t.key}`}
              className={`px-4 py-2 text-xs font-mono rounded-t transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 ${
                activeTab === t.key
                  ? "bg-surface-1 text-zinc-100 border border-white/5 border-b-0"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div
          className="bg-surface-0 border border-white/5 rounded-lg p-4"
          role="tabpanel"
          id={`tabpanel-${activeTab}`}
          aria-labelledby={activeTab}
        >
          {/* Controls are in the global bar above tabs */}

          {activeTab === "overview" && (
            <div className="space-y-6">
              {/* Performance charts */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <PerformanceChart
                  models={visibleModels}
                  metric="mention_count"
                  label="Mentions Extracted"
                  tooltip={METRIC_TOOLTIPS.mentions}
                />
                <PerformanceChart
                  models={visibleModels}
                  metric="assertion_count"
                  label="Assertions Extracted"
                  tooltip={METRIC_TOOLTIPS.assertions}
                />
                <PerformanceChart
                  models={visibleModels}
                  metric="tokens_per_sec"
                  label="Speed (tok/s)"
                  format={(v) => `${v.toFixed(0)}`}
                  tooltip={METRIC_TOOLTIPS.tokens_per_sec}
                />
                <PerformanceChart
                  models={visibleModels}
                  metric="duration_s"
                  label="Total Time"
                  format={(v) => `${v.toFixed(1)}s`}
                  tooltip={METRIC_TOOLTIPS.duration}
                />
              </div>

              {/* Model stats table */}
              <ModelStatsTable models={visibleModels} groupBy={groupBy} />
            </div>
          )}

          {activeTab === "scores" && (
            <ScoresTab report={report} visibleModels={visibleModels} groupBy={groupBy} />
          )}

          {activeTab === "entities" && (
            <EntityMatrix
              entities={report.entities}
              modelNames={report.model_names}
              onSelectEntity={setSelectedEntity}
              selectedEntityKey={
                selectedEntity
                  ? `${selectedEntity.domain}::${selectedEntity.consensus_type}::${selectedEntity.text}`
                  : null
              }
            />
          )}

          {activeTab === "propositions" && (
            <PropositionMatrix propositions={report.propositions} modelNames={report.model_names} />
          )}

          {activeTab === "domains" && (
            <DomainBreakdown report={report} visibleModels={visibleModels} groupBy={groupBy} />
          )}

          {activeTab === "pipeline" && <PipelineTable models={visibleModels} groupBy={groupBy} />}

          {activeTab === "audit" && (
            <div className="space-y-4">
              <LiveGantt />
              <AuditViewer modelNames={report.model_names} />
            </div>
          )}

          {activeTab === "ground-truth" && (
            <GroundTruthPanel selectedGT={selectedGT} onSelectGT={setSelectedGT} />
          )}
        </div>
      </div>
      {/* Entity-detail side drawer — only relevant on the entities tab; clears when switching tabs */}
      {activeTab === "entities" && (
        <EntityJsonPanel entity={selectedEntity} onClose={() => setSelectedEntity(null)} />
      )}
    </div>
  );
}
