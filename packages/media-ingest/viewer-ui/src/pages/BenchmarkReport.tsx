import { useEffect, useState } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@thebranchdriftcatalyst/catalyst-ui";
import type {
  BenchmarkReport as BenchmarkReportType,
  ModelResult,
  ModelScores,
  EntityRow,
  PropositionRow,
} from "@/types/benchmark";

// ── Metric Tooltip Definitions ────────────────────────────────────────
const METRIC_TOOLTIPS: Record<string, string> = {
  // F1 / Precision / Recall
  f1: "Harmonic mean of precision and recall. Range 0\u20131, higher is better. F1 = 2\u00d7(P\u00d7R)/(P+R). Punishes imbalance \u2014 99% precision + 1% recall \u2248 2% F1.",
  precision:
    "Of everything predicted positive, how many were actually correct? Higher = fewer false positives.",
  recall:
    "Of everything actually positive, how many did the model catch? Higher = fewer missed entities.",
  strict_f1:
    "Text AND entity type must both match ground truth. F1 = 2\u00d7(P\u00d7R)/(P+R), range 0\u20131, higher is better.",
  relaxed_f1:
    "Only text must match (type can differ). F1 = 2\u00d7(P\u00d7R)/(P+R), range 0\u20131, higher is better.",
  strict_precision:
    "Of predicted entities, fraction where both text and type match ground truth. Higher = fewer false positives.",
  strict_recall:
    "Of ground-truth entities, fraction where both text and type were found. Higher = fewer misses.",
  relaxed_precision:
    "Of predicted entities, fraction where text matches ground truth (type ignored). Higher = fewer false positives.",
  relaxed_recall:
    "Of ground-truth entities, fraction where text was found (type ignored). Higher = fewer misses.",
  type_accuracy:
    "Among text-matched entities, fraction with the correct entity type. Range 0\u20131, higher is better.",
  span_accuracy:
    "Fraction where source_text[start:end] == entity_text. Measures offset precision. Range 0\u20131, higher is better.",
  hallucination_rate:
    "1 \u2212 span_accuracy. Entities returned that don\u2019t exist at the claimed position in source text. Lower is better.",
  // Speed / efficiency
  quality_speed_ratio:
    "F1 \u00f7 duration. Higher = better extraction quality per second of compute. Best \u201cbang for buck\u201d metric.",
  per_chunk_latency:
    "Total time \u00f7 number of chunks. Average time to process one text chunk. Lower is better.",
  tokens_per_sec: "Estimated throughput in tokens per second. Higher = faster model.",
  duration: "Wall-clock time for the full extraction run. Lower is better.",
  // Counts
  mentions: "Named entities extracted (PERSON, ORG, GPE, etc.). Count across all chunks.",
  assertions: "Subject\u2013Predicate\u2013Object triples extracted. Count across all chunks.",
  retries: "Number of MCP validation repair cycles needed. 0 = passed first try.",
  errors: "Chunks where extraction failed completely. 0 is ideal.",
  // Aggregate labels
  models: "Total number of models evaluated in this benchmark run.",
  unique_entities: "Distinct entity texts found across all models after deduplication.",
  propositions: "Distinct Subject\u2013Predicate\u2013Object triples extracted across all models.",
  domains: "Number of source-text domains (e.g. media, congress, leaks) included in the benchmark.",
  fastest: "Model with the shortest wall-clock extraction time.",
  // Proposition scores
  proposition_strict_f1:
    "SPO triple must match exactly (subject, predicate, and object). F1 range 0\u20131, higher is better.",
  proposition_relaxed_f1:
    "SPO triple with fuzzy matching on subject/object text. F1 range 0\u20131, higher is better.",
  // Entity matrix columns
  entity_text: "The surface text of the extracted entity.",
  entity_domain: "Source-text domain this entity was found in.",
  entity_type: "Consensus entity type across models (e.g. PERSON, ORG, GPE).",
  entity_model_count: "Number of models that extracted this entity.",
  // Proposition matrix columns
  subject: "The subject of the extracted triple.",
  predicate: "The relationship/verb connecting subject to object.",
  object: "The object of the extracted triple.",
  prop_model_count: "Number of models that extracted this triple.",
  // Pipeline stages
  extract_mentions: "LLM call to extract named entities from text chunks.",
  validate_mentions: "MCP schema validation of extracted mentions.",
  repair_mentions: "Automatic repair cycle for mentions that failed validation.",
  extract_propositions: "LLM call to extract SPO triples from text chunks.",
  validate_propositions: "MCP schema validation of extracted propositions.",
  repair_propositions: "Automatic repair cycle for propositions that failed validation.",
  persist_artifacts: "Write validated extractions to storage.",
  failure_handler: "Chunks that failed all extraction/repair attempts.",
};

function MetricLabel({ label, tooltip }: { label: string; tooltip?: string }) {
  if (!tooltip) return <span>{label}</span>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help border-b border-dotted border-zinc-600">{label}</span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}

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

const DOMAIN_COLORS: Record<string, string> = {
  media: "bg-violet-500/20 text-violet-300",
  congress: "bg-blue-500/20 text-blue-300",
  open_leaks: "bg-rose-500/20 text-rose-300",
  unknown: "bg-zinc-500/20 text-zinc-300",
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

function DomainBadge({ domain }: { domain: string }) {
  const colors = DOMAIN_COLORS[domain] || DOMAIN_COLORS.unknown;
  const labels: Record<string, string> = {
    media: "MEDIA",
    congress: "CONGRESS",
    open_leaks: "LEAKS",
  };
  return (
    <span className={`inline-block px-1.5 py-0.5 text-[9px] font-mono uppercase rounded ${colors}`}>
      {labels[domain] || domain}
    </span>
  );
}

function StatCard({
  label,
  value,
  sub,
  tooltip,
}: {
  label: string;
  value: string | number;
  sub?: string;
  tooltip?: string;
}) {
  return (
    <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
      <div className="text-zinc-500 text-xs font-mono uppercase tracking-wider">
        {tooltip ? <MetricLabel label={label} tooltip={tooltip} /> : label}
      </div>
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
  tooltip,
}: {
  models: ModelResult[];
  metric: keyof ModelResult["stats"];
  label: string;
  format?: (v: number) => string;
  tooltip?: string;
}) {
  const max = Math.max(...models.map((m) => m.stats[metric] as number), 1);
  const fmt = format || ((v: number) => String(v));
  const sorted = [...models].sort(
    (a, b) => (b.stats[metric] as number) - (a.stats[metric] as number),
  );

  return (
    <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
      <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-3">
        {tooltip ? <MetricLabel label={label} tooltip={tooltip} /> : label}
      </h3>
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
            <th className="text-left py-2 px-2 sticky left-0 bg-surface-0">
              <MetricLabel label="Entity" tooltip={METRIC_TOOLTIPS.entity_text} />
            </th>
            <th className="text-left py-2 px-1">
              <MetricLabel label="Domain" tooltip={METRIC_TOOLTIPS.entity_domain} />
            </th>
            <th className="text-left py-2 px-1">
              <MetricLabel label="Type" tooltip={METRIC_TOOLTIPS.entity_type} />
            </th>
            <th className="text-center py-2 px-1">
              <MetricLabel label="#" tooltip={METRIC_TOOLTIPS.entity_model_count} />
            </th>
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
                <DomainBadge domain={e.domain || "unknown"} />
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
            <th className="text-left py-2 px-2">
              <MetricLabel label="Subject" tooltip={METRIC_TOOLTIPS.subject} />
            </th>
            <th className="text-left py-2 px-1">
              <MetricLabel label="Predicate" tooltip={METRIC_TOOLTIPS.predicate} />
            </th>
            <th className="text-left py-2 px-1">
              <MetricLabel label="Object" tooltip={METRIC_TOOLTIPS.object} />
            </th>
            <th className="text-center py-2 px-1">
              <MetricLabel label="#" tooltip={METRIC_TOOLTIPS.prop_model_count} />
            </th>
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
                <span className="text-[10px]">
                  {METRIC_TOOLTIPS[s.key] ? (
                    <MetricLabel label={s.label} tooltip={METRIC_TOOLTIPS[s.key]} />
                  ) : (
                    s.label
                  )}
                </span>
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

// ── Score Bar Chart ───────────────────────────────────────────────────
function ScoreBarChart({
  models,
  metricKey,
  label,
  format,
  isPercentage = true,
  invertSort = false,
  tooltip,
}: {
  models: ModelResult[];
  metricKey: keyof ModelScores;
  label: string;
  format?: (v: number) => string;
  isPercentage?: boolean;
  invertSort?: boolean;
  tooltip?: string;
}) {
  const scored = models.filter((m) => m.scores != null);
  const sorted = [...scored].sort((a, b) => {
    const diff = (b.scores![metricKey] as number) - (a.scores![metricKey] as number);
    return invertSort ? -diff : diff;
  });

  const max = Math.max(...scored.map((m) => m.scores![metricKey] as number), 0.001);
  const fmt =
    format ||
    (isPercentage ? (v: number) => `${(v * 100).toFixed(1)}%` : (v: number) => v.toFixed(2));

  return (
    <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
      <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-3">
        {tooltip ? <MetricLabel label={label} tooltip={tooltip} /> : label}
      </h3>
      <div className="space-y-1.5">
        {sorted.map((m) => {
          const val = m.scores![metricKey] as number;
          const pct = isPercentage ? val * 100 : (val / max) * 100;
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

// ── Precision/Recall Table ────────────────────────────────────────────
function PrecisionRecallTable({ models }: { models: ModelResult[] }) {
  const scored = models.filter((m) => m.scores != null);
  const sorted = [...scored].sort(
    (a, b) => (b.scores!.mention_strict_f1 as number) - (a.scores!.mention_strict_f1 as number),
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            <th className="text-left py-2 px-2">Model</th>
            <th className="text-left py-2 px-1">Type</th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="M Strict P" tooltip={METRIC_TOOLTIPS.strict_precision} />
            </th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="M Strict R" tooltip={METRIC_TOOLTIPS.strict_recall} />
            </th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="M Strict F1" tooltip={METRIC_TOOLTIPS.strict_f1} />
            </th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="M Relax P" tooltip={METRIC_TOOLTIPS.relaxed_precision} />
            </th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="M Relax R" tooltip={METRIC_TOOLTIPS.relaxed_recall} />
            </th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="M Relax F1" tooltip={METRIC_TOOLTIPS.relaxed_f1} />
            </th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="Type Acc" tooltip={METRIC_TOOLTIPS.type_accuracy} />
            </th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="P Strict F1" tooltip={METRIC_TOOLTIPS.proposition_strict_f1} />
            </th>
            <th className="text-center py-2 px-2">
              <MetricLabel label="P Relax F1" tooltip={METRIC_TOOLTIPS.proposition_relaxed_f1} />
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => {
            const s = m.scores!;
            const fmtCell = (v: number) => {
              const pct = v * 100;
              const color =
                pct >= 80 ? "text-emerald-400" : pct >= 50 ? "text-amber-400" : "text-red-400";
              return <span className={color}>{pct.toFixed(1)}%</span>;
            };
            return (
              <tr key={m.name} className="border-b border-white/5 hover:bg-white/[0.02]">
                <td className="py-1.5 px-2 text-zinc-200">{m.name}</td>
                <td className="py-1.5 px-1">
                  <ModelTypeBadge type={m.type} />
                </td>
                <td className="py-1.5 px-2 text-center">{fmtCell(s.mention_strict_precision)}</td>
                <td className="py-1.5 px-2 text-center">{fmtCell(s.mention_strict_recall)}</td>
                <td className="py-1.5 px-2 text-center font-bold">
                  {fmtCell(s.mention_strict_f1)}
                </td>
                <td className="py-1.5 px-2 text-center">{fmtCell(s.mention_relaxed_precision)}</td>
                <td className="py-1.5 px-2 text-center">{fmtCell(s.mention_relaxed_recall)}</td>
                <td className="py-1.5 px-2 text-center font-bold">
                  {fmtCell(s.mention_relaxed_f1)}
                </td>
                <td className="py-1.5 px-2 text-center">{fmtCell(s.mention_type_accuracy)}</td>
                <td className="py-1.5 px-2 text-center font-bold">
                  {fmtCell(s.proposition_strict_f1)}
                </td>
                <td className="py-1.5 px-2 text-center font-bold">
                  {fmtCell(s.proposition_relaxed_f1)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

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
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
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
          metricKey="hallucination_rate"
          label="Hallucination Rate (lower is better)"
          invertSort={true}
          tooltip={METRIC_TOOLTIPS.hallucination_rate}
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

      {/* Full precision/recall table */}
      <div>
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-3">
          Precision / Recall / F1 Breakdown
        </h3>
        <PrecisionRecallTable models={report.models} />
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────
export default function BenchmarkReport() {
  const [report, setReport] = useState<BenchmarkReportType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<
    "overview" | "scores" | "entities" | "propositions" | "pipeline"
  >("overview");

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
                        <div className="text-zinc-500">
                          <MetricLabel label="Mentions" tooltip={METRIC_TOOLTIPS.mentions} />
                        </div>
                        <div className="text-zinc-300 text-right">{m.stats.mention_count}</div>
                        <div className="text-zinc-500">
                          <MetricLabel label="Assertions" tooltip={METRIC_TOOLTIPS.assertions} />
                        </div>
                        <div className="text-zinc-300 text-right">{m.stats.assertion_count}</div>
                        <div className="text-zinc-500">
                          <MetricLabel label="Time" tooltip={METRIC_TOOLTIPS.duration} />
                        </div>
                        <div className="text-zinc-300 text-right">
                          {m.stats.duration_s.toFixed(1)}s
                        </div>
                        <div className="text-zinc-500">
                          <MetricLabel label="Tok/s" tooltip={METRIC_TOOLTIPS.tokens_per_sec} />
                        </div>
                        <div className="text-zinc-300 text-right">
                          {m.stats.tokens_per_sec.toFixed(0)}
                        </div>
                        <div className="text-zinc-500">
                          <MetricLabel label="Retries" tooltip={METRIC_TOOLTIPS.retries} />
                        </div>
                        <div
                          className={`text-right ${m.stats.mention_retries + m.stats.proposition_retries > 0 ? "text-amber-400" : "text-zinc-300"}`}
                        >
                          {m.stats.mention_retries + m.stats.proposition_retries}
                        </div>
                        <div className="text-zinc-500">
                          <MetricLabel label="Errors" tooltip={METRIC_TOOLTIPS.errors} />
                        </div>
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
              <PipelineBreakdown models={report.models} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
