import { useMemo } from "react";
import type {
  BenchmarkReport as BenchmarkReportType,
  EntityRow,
  ModelResult,
  PropositionRow,
} from "@/types/benchmark";
import { DomainBadge, GroupBadge, MetricLabel, METRIC_TOOLTIPS } from "./shared";
import type { GroupByDimension } from "./shared";
import { useModelGrouping } from "./useModelGrouping";

// Tooltips specific to the domain breakdown view. Re-uses METRIC_TOOLTIPS
// where possible; adds a couple of new keys for the per-domain coverage view.
const DOMAIN_TOOLTIPS: Record<string, string> = {
  domain_chunks: "Number of source-text chunks belonging to this domain in the benchmark corpus.",
  domain_entity_coverage:
    "Unique entities a model extracted from chunks in this domain. Higher = better entity coverage for that domain.",
  domain_proposition_coverage: "Unique SPO triples a model extracted from chunks in this domain.",
  domain_share:
    "What fraction of this model's total extractions came from this domain. A balanced model spreads roughly proportional to chunk distribution.",
};

const DOMAIN_ORDER: Record<string, number> = {
  media: 0,
  congress: 1,
  open_leaks: 2,
  unknown: 99,
};

// Stacked bar palette for domains (matches DOMAIN_COLORS hue family).
const DOMAIN_BAR_COLORS: Record<string, string> = {
  media: "bg-violet-500",
  congress: "bg-blue-500",
  open_leaks: "bg-rose-500",
  unknown: "bg-zinc-500",
};

interface DomainModelCounts {
  // domain -> count of unique items (entities or propositions) this model extracted from that domain
  byDomain: Record<string, number>;
  total: number;
}

function aggregateByDomain(
  entities: EntityRow[] | PropositionRow[],
  modelNames: string[],
  isProposition: boolean,
): Record<string, DomainModelCounts> {
  // model -> { byDomain, total }
  const out: Record<string, DomainModelCounts> = {};
  for (const name of modelNames) {
    out[name] = { byDomain: {}, total: 0 };
  }

  for (const row of entities) {
    const domain = row.domain || "unknown";
    if (isProposition) {
      const prop = row as PropositionRow;
      for (const name of prop.models) {
        if (!out[name]) continue;
        out[name].byDomain[domain] = (out[name].byDomain[domain] || 0) + 1;
        out[name].total += 1;
      }
    } else {
      const ent = row as EntityRow;
      for (const name of Object.keys(ent.models)) {
        if (!out[name]) continue;
        out[name].byDomain[domain] = (out[name].byDomain[domain] || 0) + 1;
        out[name].total += 1;
      }
    }
  }
  return out;
}

// ── Stacked Bar Per Model ─────────────────────────────────────────────

function StackedBar({
  counts,
  domains,
  max,
}: {
  counts: DomainModelCounts;
  domains: string[];
  max: number;
}) {
  const total = counts.total;
  if (total === 0 || max === 0) return <div className="h-4 bg-surface-0 rounded" />;
  const widthPct = (total / max) * 100;
  return (
    <div
      className="flex h-4 bg-surface-0 rounded overflow-hidden"
      style={{ width: `${Math.max(widthPct, 2)}%` }}
    >
      {domains.map((d) => {
        const v = counts.byDomain[d] || 0;
        if (v === 0) return null;
        const segPct = (v / total) * 100;
        const color = DOMAIN_BAR_COLORS[d] || DOMAIN_BAR_COLORS.unknown;
        return (
          <div
            key={d}
            className={`${color}`}
            title={`${d}: ${v}`}
            style={{ width: `${segPct}%` }}
          />
        );
      })}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────

export function DomainBreakdown({
  report,
  visibleModels,
  groupBy,
}: {
  report: BenchmarkReportType;
  visibleModels: ModelResult[];
  groupBy: GroupByDimension;
}) {
  const domains = useMemo(() => {
    const set = new Set<string>();
    for (const e of report.entities) set.add(e.domain || "unknown");
    for (const p of report.propositions) set.add(p.domain || "unknown");
    for (const d of Object.keys(report.domains || {})) set.add(d);
    return Array.from(set).sort((a, b) => (DOMAIN_ORDER[a] ?? 50) - (DOMAIN_ORDER[b] ?? 50));
  }, [report.entities, report.propositions, report.domains]);

  const visibleNames = useMemo(() => visibleModels.map((m) => m.name), [visibleModels]);

  const entityCoverage = useMemo(
    () => aggregateByDomain(report.entities, visibleNames, false),
    [report.entities, visibleNames],
  );
  const propCoverage = useMemo(
    () => aggregateByDomain(report.propositions, visibleNames, true),
    [report.propositions, visibleNames],
  );

  // Per-domain unique-item totals (for "X / total" denominator displays).
  const domainEntityTotals = useMemo(() => {
    const m: Record<string, number> = {};
    for (const e of report.entities) {
      const d = e.domain || "unknown";
      m[d] = (m[d] || 0) + 1;
    }
    return m;
  }, [report.entities]);

  const domainPropTotals = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of report.propositions) {
      const d = p.domain || "unknown";
      m[d] = (m[d] || 0) + 1;
    }
    return m;
  }, [report.propositions]);

  // Largest total across visible models, for stacked-bar scaling.
  const maxEntityTotal = useMemo(
    () => Math.max(1, ...visibleNames.map((n) => entityCoverage[n]?.total ?? 0)),
    [entityCoverage, visibleNames],
  );
  // Rows for grouping: one per visible model.
  const rows = useMemo(
    () => visibleModels.map((m) => ({ name: m.name, type: m.type })),
    [visibleModels],
  );
  const entityGroups = useModelGrouping(visibleModels, rows, groupBy);

  if (visibleModels.length === 0) {
    return (
      <div className="text-zinc-500 text-sm py-4">
        No models visible. Toggle some models in the controls bar.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Chunk distribution overview ──────────────────────────── */}
      <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
        <h3 className="text-xs font-mono text-zinc-300 uppercase tracking-wider mb-3">
          <MetricLabel label="Corpus by Domain" tooltip={DOMAIN_TOOLTIPS.domain_chunks} />
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {domains.map((d) => {
            const chunks = report.domains?.[d] ?? 0;
            const ec = domainEntityTotals[d] ?? 0;
            const pc = domainPropTotals[d] ?? 0;
            return (
              <div
                key={d}
                className="bg-surface-0 border border-white/5 rounded p-3 flex flex-col gap-1"
              >
                <DomainBadge domain={d} />
                <div className="flex items-baseline gap-2 mt-1">
                  <span className="text-xl font-mono text-zinc-100">{chunks}</span>
                  <span className="text-[11px] text-zinc-500 font-mono">chunks</span>
                </div>
                <div className="text-[11px] text-zinc-400 font-mono">
                  {ec} entities · {pc} propositions
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Per-model stacked bar: entity extraction by domain ───── */}
      <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
        <h3 className="text-xs font-mono text-zinc-300 uppercase tracking-wider mb-1">
          <MetricLabel
            label="Entity Extraction Mix by Domain"
            tooltip={DOMAIN_TOOLTIPS.domain_share}
          />
        </h3>
        <p className="text-[11px] text-zinc-500 font-mono mb-3">
          Bar length = total unique entities. Segments show distribution across domains.
        </p>
        <DomainLegend domains={domains} />
        <div className="space-y-1.5 mt-3">
          {entityGroups.map(([groupKey, groupRows]) => (
            <GroupSection
              key={groupKey}
              groupKey={groupKey}
              groupBy={groupBy}
              rows={groupRows}
              counts={entityCoverage}
              domains={domains}
              max={maxEntityTotal}
            />
          ))}
        </div>
      </div>

      {/* ── Per-model x per-domain entity coverage matrix ────────── */}
      <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
        <h3 className="text-xs font-mono text-zinc-300 uppercase tracking-wider mb-3">
          <MetricLabel
            label="Per-Domain Entity Coverage"
            tooltip={DOMAIN_TOOLTIPS.domain_entity_coverage}
          />
        </h3>
        <CoverageMatrix
          rows={rows}
          counts={entityCoverage}
          domains={domains}
          domainTotals={domainEntityTotals}
          groups={entityGroups}
          groupBy={groupBy}
          unitLabel="entities"
        />
      </div>

      {/* ── Per-model x per-domain proposition coverage matrix ───── */}
      {report.proposition_count > 0 && (
        <div className="bg-surface-1 border border-white/5 rounded-lg p-4">
          <h3 className="text-xs font-mono text-zinc-300 uppercase tracking-wider mb-3">
            <MetricLabel
              label="Per-Domain Proposition Coverage"
              tooltip={DOMAIN_TOOLTIPS.domain_proposition_coverage}
            />
          </h3>
          <CoverageMatrix
            rows={rows}
            counts={propCoverage}
            domains={domains}
            domainTotals={domainPropTotals}
            groups={entityGroups}
            groupBy={groupBy}
            unitLabel="propositions"
          />
        </div>
      )}
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────

function DomainLegend({ domains }: { domains: string[] }) {
  return (
    <div className="flex flex-wrap gap-3">
      {domains.map((d) => (
        <div key={d} className="flex items-center gap-1.5 text-[11px] font-mono text-zinc-400">
          <span
            className={`inline-block w-3 h-3 rounded ${DOMAIN_BAR_COLORS[d] || DOMAIN_BAR_COLORS.unknown}`}
          />
          {d}
        </div>
      ))}
    </div>
  );
}

function GroupSection({
  groupKey,
  groupBy,
  rows,
  counts,
  domains,
  max,
}: {
  groupKey: string;
  groupBy: GroupByDimension;
  rows: Array<{ name: string; type: string }>;
  counts: Record<string, DomainModelCounts>;
  domains: string[];
  max: number;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 pt-2">
        <GroupBadge groupKey={groupKey} dimension={groupBy} />
        <span className="text-[11px] text-zinc-500 font-mono">
          ({rows.length} model{rows.length !== 1 ? "s" : ""})
        </span>
      </div>
      {rows.map((r) => {
        const c = counts[r.name] ?? { byDomain: {}, total: 0 };
        return (
          <div key={r.name} className="flex items-center gap-2">
            <div className="w-32 text-xs text-zinc-400 font-mono truncate">{r.name}</div>
            <div className="flex-1">
              <StackedBar counts={c} domains={domains} max={max} />
            </div>
            <div className="w-16 text-xs text-zinc-300 font-mono text-right">{c.total}</div>
          </div>
        );
      })}
    </div>
  );
}

function CoverageMatrix({
  counts,
  domains,
  domainTotals,
  groups,
  groupBy,
  unitLabel,
}: {
  rows: Array<{ name: string; type: string }>;
  counts: Record<string, DomainModelCounts>;
  domains: string[];
  domainTotals: Record<string, number>;
  groups: [string, Array<{ name: string; type: string }>][];
  groupBy: GroupByDimension;
  unitLabel: string;
}) {
  // For colour-grading per cell, use coverage ratio = found / total-in-domain.
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-400 border-b border-white/5">
            <th className="text-left py-2 px-2">Model</th>
            {domains.map((d) => (
              <th key={d} className="text-center py-2 px-1 min-w-[90px]">
                <div className="flex flex-col items-center gap-1">
                  <DomainBadge domain={d} />
                  <span className="text-[10px] text-zinc-500">
                    /{domainTotals[d] ?? 0} {unitLabel}
                  </span>
                </div>
              </th>
            ))}
            <th className="text-center py-2 px-2">
              <MetricLabel label="Total" tooltip={METRIC_TOOLTIPS.unique_entities} />
            </th>
          </tr>
        </thead>
        <tbody>
          {groups.map(([groupKey, groupRows]) => {
            // Row-by-row, plus a group-summary row at top
            return (
              <GroupBlock key={groupKey}>
                <tr className="bg-white/[0.03] border-b border-white/5">
                  <td className="py-2 px-2">
                    <div className="flex items-center gap-2">
                      <GroupBadge groupKey={groupKey} dimension={groupBy} />
                      <span className="text-zinc-500 text-[11px]">({groupRows.length})</span>
                    </div>
                  </td>
                  {domains.map((d) => {
                    const groupTotal = groupRows.reduce(
                      (s, r) => s + (counts[r.name]?.byDomain[d] ?? 0),
                      0,
                    );
                    return (
                      <td key={d} className="py-1.5 px-1 text-center text-[11px] text-zinc-500">
                        Σ{groupTotal}
                      </td>
                    );
                  })}
                  <td className="py-1.5 px-2 text-center text-[11px] text-zinc-500">
                    Σ{groupRows.reduce((s, r) => s + (counts[r.name]?.total ?? 0), 0)}
                  </td>
                </tr>
                {groupRows.map((r) => {
                  const c = counts[r.name] ?? { byDomain: {}, total: 0 };
                  return (
                    <tr key={r.name} className="border-b border-white/5 hover:bg-white/[0.02]">
                      <td className="py-1.5 px-2 text-left text-zinc-200">{r.name}</td>
                      {domains.map((d) => {
                        const v = c.byDomain[d] ?? 0;
                        const total = domainTotals[d] ?? 0;
                        return (
                          <td key={d} className="py-1.5 px-1 text-center">
                            <CoverageCell value={v} total={total} />
                          </td>
                        );
                      })}
                      <td className="py-1.5 px-2 text-center text-zinc-300">{c.total}</td>
                    </tr>
                  );
                })}
              </GroupBlock>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CoverageCell({ value, total }: { value: number; total: number }) {
  if (total === 0 || value === 0) {
    return <span className="text-zinc-700">&mdash;</span>;
  }
  const ratio = value / total;
  const color =
    ratio >= 0.5 ? "text-emerald-400" : ratio >= 0.2 ? "text-amber-400" : "text-zinc-300";
  return (
    <span className={color}>
      {value}
      <span className="text-zinc-600 text-[10px]">/{total}</span>
    </span>
  );
}

function GroupBlock({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
