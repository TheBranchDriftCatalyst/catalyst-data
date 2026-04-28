import { useEffect, useState } from "react";
import type {
  BenchmarkReport as BenchmarkReportType,
  ModelResult,
  EntityRow,
  PropositionRow,
} from "@/types/benchmark";

// Entity type colors matching index.css entity highlights
const TYPE_COLORS: Record<string, string> = {
  PERSON: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  ORG: "bg-purple-500/20 text-purple-300 border-purple-500/30",
  GPE: "bg-green-500/20 text-green-300 border-green-500/30",
  LOC: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  DATE: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  LAW: "bg-red-500/20 text-red-300 border-red-500/30",
  EVENT: "bg-pink-500/20 text-pink-300 border-pink-500/30",
  MONEY: "bg-yellow-500/20 text-yellow-300 border-yellow-500/30",
  NORP: "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
  DOCUMENT: "bg-orange-500/20 text-orange-300 border-orange-500/30",
  FACILITY: "bg-teal-500/20 text-teal-300 border-teal-500/30",
  ROLE: "bg-indigo-500/20 text-indigo-300 border-indigo-500/30",
  OTHER: "bg-zinc-500/20 text-zinc-300 border-zinc-500/30",
};

const MODEL_TYPE_COLORS: Record<string, string> = {
  encoder: "bg-emerald-500/20 text-emerald-300",
  specialist: "bg-amber-500/20 text-amber-300",
  llm: "bg-blue-500/20 text-blue-300",
};

const PIPELINE_STAGES = [
  { key: "extract_mentions", label: "Extract Mentions" },
  { key: "validate_mentions", label: "Validate Mentions" },
  { key: "repair_mentions", label: "Repair Mentions" },
  { key: "extract_propositions", label: "Extract Props" },
  { key: "validate_propositions", label: "Validate Props" },
  { key: "repair_propositions", label: "Repair Props" },
  { key: "persist_artifacts", label: "Persist" },
  { key: "failure_handler", label: "Failed" },
];

function TypeBadge({ type }: { type: string }) {
  const colors = TYPE_COLORS[type] || TYPE_COLORS.OTHER;
  return (
    <span className={`inline-block px-1.5 py-0.5 text-[10px] font-mono rounded border ${colors}`}>
      {type}
    </span>
  );
}

function ModelTypeBadge({ type }: { type: string }) {
  const colors = MODEL_TYPE_COLORS[type] || MODEL_TYPE_COLORS.llm;
  return (
    <span className={`inline-block px-2 py-0.5 text-[10px] font-mono uppercase rounded ${colors}`}>
      {type}
    </span>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
      <div className="text-zinc-500 text-xs font-mono uppercase tracking-wider">{label}</div>
      <div className="text-2xl font-mono text-zinc-100 mt-1">{value}</div>
      {sub && <div className="text-xs text-zinc-500 mt-1">{sub}</div>}
    </div>
  );
}

// ── Performance Bar Chart ──────────────────────────────────────────────
function PerformanceChart({
  models,
  metric,
  label,
  format,
}: {
  models: ModelResult[];
  metric: keyof ModelResult["stats"];
  label: string;
  format?: (v: number) => string;
}) {
  const max = Math.max(...models.map((m) => m.stats[metric] as number), 1);
  const fmt = format || ((v: number) => String(v));
  const sorted = [...models].sort(
    (a, b) => (b.stats[metric] as number) - (a.stats[metric] as number),
  );

  return (
    <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
      <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-3">{label}</h3>
      <div className="space-y-1.5">
        {sorted.map((m) => {
          const val = m.stats[metric] as number;
          const pct = (val / max) * 100;
          const barColor =
            m.type === "encoder"
              ? "bg-emerald-500"
              : m.type === "specialist"
                ? "bg-amber-500"
                : "bg-blue-500";
          return (
            <div key={m.name} className="flex items-center gap-2">
              <div className="w-28 text-xs text-zinc-400 font-mono truncate">{m.name}</div>
              <div className="flex-1 h-4 bg-surface-0 rounded overflow-hidden">
                <div
                  className={`h-full ${barColor} rounded transition-all`}
                  style={{ width: `${Math.max(pct, 2)}%` }}
                />
              </div>
              <div className="w-16 text-xs text-zinc-300 font-mono text-right">{fmt(val)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Entity Matrix ──────────────────────────────────────────────────────
function EntityMatrix({ entities, modelNames }: { entities: EntityRow[]; modelNames: string[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            <th className="text-left py-2 px-2 sticky left-0 bg-surface-0">Entity</th>
            <th className="text-left py-2 px-1">Type</th>
            <th className="text-center py-2 px-1">#</th>
            {modelNames.map((n) => (
              <th key={n} className="text-center py-2 px-1 min-w-[60px]">
                <span className="writing-mode-vertical text-[10px]">
                  {n.replace(/-/g, "\u200b-")}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entities.map((e, i) => (
            <tr key={`${e.text}-${i}`} className="border-b border-white/5 hover:bg-white/[0.02]">
              <td className="py-1.5 px-2 text-zinc-200 sticky left-0 bg-surface-0 max-w-[200px] truncate">
                {e.text}
              </td>
              <td className="py-1.5 px-1">
                <TypeBadge type={e.consensus_type} />
              </td>
              <td className="py-1.5 px-1 text-center text-zinc-400">{e.model_count}</td>
              {modelNames.map((name) => {
                const info = e.models[name];
                if (!info) {
                  return (
                    <td key={name} className="py-1.5 px-1 text-center text-zinc-700">
                      ·
                    </td>
                  );
                }
                const isConsensus = info.type === e.consensus_type;
                return (
                  <td
                    key={name}
                    className={`py-1.5 px-1 text-center ${isConsensus ? "text-emerald-400" : "text-amber-400"}`}
                    title={`${info.type} (${(info.confidence * 100).toFixed(0)}%)`}
                  >
                    {isConsensus ? "✓" : info.type.slice(0, 3)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── SPO Matrix ─────────────────────────────────────────────────────────
function PropositionMatrix({
  propositions,
  modelNames,
}: {
  propositions: PropositionRow[];
  modelNames: string[];
}) {
  if (propositions.length === 0) {
    return (
      <div className="text-zinc-500 text-sm py-4">No propositions extracted by any model.</div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            <th className="text-left py-2 px-2">Subject</th>
            <th className="text-left py-2 px-1">Predicate</th>
            <th className="text-left py-2 px-1">Object</th>
            <th className="text-center py-2 px-1">#</th>
            {modelNames.map((n) => (
              <th key={n} className="text-center py-2 px-1 min-w-[60px]">
                <span className="text-[10px]">{n.replace(/-/g, "\u200b-")}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {propositions.slice(0, 40).map((p, i) => (
            <tr key={i} className="border-b border-white/5 hover:bg-white/[0.02]">
              <td className="py-1.5 px-2 text-zinc-200 max-w-[150px] truncate">{p.subject}</td>
              <td className="py-1.5 px-1 text-cyan-400">{p.predicate}</td>
              <td className="py-1.5 px-1 text-zinc-200 max-w-[150px] truncate">{p.object}</td>
              <td className="py-1.5 px-1 text-center text-zinc-400">{p.model_count}</td>
              {modelNames.map((name) => (
                <td
                  key={name}
                  className={`py-1.5 px-1 text-center ${p.models.includes(name) ? "text-emerald-400" : "text-zinc-700"}`}
                >
                  {p.models.includes(name) ? "✓" : "·"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Pipeline Breakdown ─────────────────────────────────────────────────
function PipelineBreakdown({ models }: { models: ModelResult[] }) {
  const modelsWithPipeline = models.filter((m) => Object.keys(m.pipeline).length > 0);
  if (modelsWithPipeline.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            <th className="text-left py-2 px-2">Model</th>
            {PIPELINE_STAGES.map((s) => (
              <th key={s.key} className="text-center py-2 px-1 min-w-[70px]">
                <span className="text-[10px]">{s.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {modelsWithPipeline.map((m) => (
            <tr key={m.name} className="border-b border-white/5 hover:bg-white/[0.02]">
              <td className="py-1.5 px-2 text-zinc-200">{m.name}</td>
              {PIPELINE_STAGES.map((s) => {
                const info = m.pipeline[s.key];
                if (!info || info.calls === 0) {
                  return (
                    <td key={s.key} className="py-1.5 px-1 text-center text-zinc-700">
                      —
                    </td>
                  );
                }
                const hasErrors = info.error > 0 || info.failed > 0;
                const hasAmbiguous = (info.ambiguous || 0) > 0;
                return (
                  <td
                    key={s.key}
                    className={`py-1.5 px-1 text-center ${hasErrors ? "text-red-400" : hasAmbiguous ? "text-amber-400" : "text-zinc-300"}`}
                  >
                    {info.calls}
                    {hasErrors && (
                      <span className="text-red-500 text-[9px]">({info.error + info.failed}e)</span>
                    )}
                    {hasAmbiguous && !hasErrors && (
                      <span className="text-amber-500 text-[9px]">({info.ambiguous}a)</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────
export default function BenchmarkReport() {
  const [report, setReport] = useState<BenchmarkReportType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"overview" | "entities" | "propositions" | "pipeline">(
    "overview",
  );

  useEffect(() => {
    fetch("/viewer/benchmark-report.json")
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}: ${r.statusText}`);
        return r.json();
      })
      .then(setReport)
      .catch((e) =>
        setError(`Could not load benchmark report: ${e.message}. Run the benchmark first.`),
      );
  }, []);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="bg-surface-1 border border-white/5 rounded-lg p-8 max-w-lg text-center">
          <h2 className="text-lg font-mono text-zinc-200 mb-2">No Benchmark Report</h2>
          <p className="text-sm text-zinc-500 mb-4">{error}</p>
          <pre className="text-xs text-zinc-400 bg-surface-0 rounded p-3 text-left">
            PYTHONPATH=. pytest tests/test_extraction_benchmark.py::TestRunAll -v -s
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

  const tabs = [
    { key: "overview" as const, label: "Overview" },
    { key: "entities" as const, label: `Entities (${report.entity_count})` },
    {
      key: "propositions" as const,
      label: `Propositions (${report.proposition_count})`,
    },
    { key: "pipeline" as const, label: "Pipeline" },
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
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Models" value={report.model_count} />
          <StatCard label="Unique Entities" value={report.entity_count} />
          <StatCard label="Propositions" value={report.proposition_count} />
          <StatCard
            label="Fastest"
            value={
              [...report.models].sort((a, b) => a.stats.duration_s - b.stats.duration_s)[0]?.name ||
              "—"
            }
            sub={`${[...report.models].sort((a, b) => a.stats.duration_s - b.stats.duration_s)[0]?.stats.duration_s.toFixed(1)}s`}
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
                />
                <PerformanceChart
                  models={report.models}
                  metric="assertion_count"
                  label="Assertions Extracted"
                />
                <PerformanceChart
                  models={report.models}
                  metric="tokens_per_sec"
                  label="Speed (tok/s)"
                  format={(v) => `${v.toFixed(0)}`}
                />
                <PerformanceChart
                  models={report.models}
                  metric="duration_s"
                  label="Total Time"
                  format={(v) => `${v.toFixed(1)}s`}
                />
              </div>

              {/* Model cards */}
              <div>
                <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-3">
                  Models by Type
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {report.models.map((m) => (
                    <div key={m.name} className="bg-surface-1 border border-white/5 rounded-lg p-3">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-mono text-zinc-200">{m.name}</span>
                        <ModelTypeBadge type={m.type} />
                      </div>
                      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
                        <div className="text-zinc-500">Mentions</div>
                        <div className="text-zinc-300 text-right">{m.stats.mention_count}</div>
                        <div className="text-zinc-500">Assertions</div>
                        <div className="text-zinc-300 text-right">{m.stats.assertion_count}</div>
                        <div className="text-zinc-500">Time</div>
                        <div className="text-zinc-300 text-right">
                          {m.stats.duration_s.toFixed(1)}s
                        </div>
                        <div className="text-zinc-500">Tok/s</div>
                        <div className="text-zinc-300 text-right">
                          {m.stats.tokens_per_sec.toFixed(0)}
                        </div>
                        <div className="text-zinc-500">Retries</div>
                        <div
                          className={`text-right ${m.stats.mention_retries + m.stats.proposition_retries > 0 ? "text-amber-400" : "text-zinc-300"}`}
                        >
                          {m.stats.mention_retries + m.stats.proposition_retries}
                        </div>
                        <div className="text-zinc-500">Errors</div>
                        <div
                          className={`text-right ${m.stats.errors > 0 ? "text-red-400" : "text-zinc-300"}`}
                        >
                          {m.stats.errors}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

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
              <PipelineBreakdown models={report.models} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
