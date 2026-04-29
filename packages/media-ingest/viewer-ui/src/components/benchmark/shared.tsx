import { Tooltip, TooltipContent, TooltipTrigger } from "@thebranchdriftcatalyst/catalyst-ui";
import type { Column } from "@tanstack/react-table";
import type { ModelResult, ModelScores } from "@/types/benchmark";

// ── Metric Tooltip Definitions ────────────────────────────────────────
export const METRIC_TOOLTIPS: Record<string, string> = {
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
  // v2 (exgraph) stage names
  extract_ner: "Generic extract node: calls LLM/encoder for NER extraction.",
  validate_ner: "MCP contract validation of NER mentions.",
  repair_ner: "Repair cycle for NER mentions that failed validation.",
  extract_spo: "Generic extract node: calls LLM for SPO triple extraction.",
  validate_spo: "MCP contract validation of SPO propositions.",
  repair_spo: "Repair cycle for propositions that failed validation.",
};

// Entity type colors matching index.css entity highlights
export const TYPE_COLORS: Record<string, string> = {
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

export const DOMAIN_COLORS: Record<string, string> = {
  media: "bg-violet-500/20 text-violet-300",
  congress: "bg-blue-500/20 text-blue-300",
  open_leaks: "bg-rose-500/20 text-rose-300",
  unknown: "bg-zinc-500/20 text-zinc-300",
};

export const MODEL_TYPE_COLORS: Record<string, string> = {
  encoder: "bg-emerald-500/20 text-emerald-300",
  specialist: "bg-amber-500/20 text-amber-300",
  llm: "bg-blue-500/20 text-blue-300",
};

// Pipeline stage display names -- supports both v1 (langgraph-aio) and v2 (exgraph) names
export const STAGE_LABELS: Record<string, string> = {
  // v1 names
  extract_mentions: "Extract NER",
  validate_mentions: "Validate NER",
  repair_mentions: "Repair NER",
  extract_propositions: "Extract SPO",
  validate_propositions: "Validate SPO",
  repair_propositions: "Repair SPO",
  persist_artifacts: "Persist",
  failure_handler: "Failed",
  // v2 (exgraph) names
  extract_ner: "Extract NER",
  validate_ner: "Validate NER",
  repair_ner: "Repair NER",
  extract_spo: "Extract SPO",
  validate_spo: "Validate SPO",
  repair_spo: "Repair SPO",
  // generic
  passthrough: "Skip",
};

// ── Shared Components ─────────────────────────────────────────────────

export function MetricLabel({ label, tooltip }: { label: string; tooltip?: string }) {
  if (!tooltip) return <span>{label}</span>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help border-b border-dotted border-zinc-600">{label}</span>
      </TooltipTrigger>
      <TooltipContent side="top" sideOffset={5}>
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}

export function TypeBadge({ type }: { type: string }) {
  const colors = TYPE_COLORS[type] || TYPE_COLORS.OTHER;
  return (
    <span className={`inline-block px-1.5 py-0.5 text-[10px] font-mono rounded border ${colors}`}>
      {type}
    </span>
  );
}

export function ModelTypeBadge({ type }: { type: string }) {
  const colors = MODEL_TYPE_COLORS[type] || MODEL_TYPE_COLORS.llm;
  return (
    <span className={`inline-block px-2 py-0.5 text-[10px] font-mono uppercase rounded ${colors}`}>
      {type}
    </span>
  );
}

export function DomainBadge({ domain }: { domain: string }) {
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

export function StatCard({
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

// ── Sortable Header for TanStack Table ────────────────────────────────

export function SortableHeader<TData>({
  column,
  children,
  className = "",
}: {
  column: Column<TData, unknown>;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      onClick={column.getToggleSortingHandler()}
      className={`cursor-pointer select-none ${className}`}
    >
      <div className="flex items-center gap-1">
        {children}
        <span className="text-zinc-400">
          {column.getIsSorted() === "asc" ? "↑" : column.getIsSorted() === "desc" ? "↓" : ""}
        </span>
      </div>
    </th>
  );
}

// ── Color-coded score cell ────────────────────────────────────────────

export function ScoreCell({ value }: { value: number }) {
  const pct = value * 100;
  const color = pct >= 80 ? "text-emerald-400" : pct >= 50 ? "text-amber-400" : "text-red-400";
  return <span className={color}>{pct.toFixed(1)}%</span>;
}

// ── Performance Bar Chart ─────────────────────────────────────────────

export function PerformanceChart({
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

// ── Score Bar Chart ──────────────────────────────────────────────────

export function ScoreBarChart({
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

// ── Model type sort order (for grouping) ──────────────────────────────

export const MODEL_TYPE_ORDER: Record<string, number> = {
  encoder: 0,
  specialist: 1,
  llm: 2,
  cloud: 3,
};
