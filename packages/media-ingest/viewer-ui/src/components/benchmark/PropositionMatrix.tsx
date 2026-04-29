import { useState, useMemo } from "react";
import type { PropositionRow } from "@/types/benchmark";
import { DomainBadge, METRIC_TOOLTIPS, MetricLabel } from "./shared";

const DOMAIN_ORDER: Record<string, number> = {
  media: 0,
  congress: 1,
  open_leaks: 2,
  unknown: 3,
};

interface GroupAgg {
  count: number;
  avgModelCount: number;
  uniquePredicates: number;
  modelCoverage: Record<string, number>;
}

export function PropositionMatrix({
  propositions,
  modelNames,
}: {
  propositions: PropositionRow[];
  modelNames: string[];
}) {
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const groups = useMemo(() => {
    const map: Record<string, PropositionRow[]> = {};
    for (const p of propositions) {
      const d = p.domain || "unknown";
      (map[d] ??= []).push(p);
    }
    return Object.entries(map).sort(
      (a, b) => (DOMAIN_ORDER[a[0]] ?? 99) - (DOMAIN_ORDER[b[0]] ?? 99),
    );
  }, [propositions]);

  // Filter out models with zero propositions
  const { active: activeModels, hidden: hiddenModels } = useMemo(() => {
    const active: string[] = [];
    const hidden: string[] = [];
    for (const name of modelNames) {
      if (propositions.some((p) => p.models.includes(name))) active.push(name);
      else hidden.push(name);
    }
    return { active, hidden };
  }, [propositions, modelNames]);

  const aggs = useMemo(() => {
    const result: Record<string, GroupAgg> = {};
    for (const [domain, rows] of groups) {
      let totalMC = 0;
      const preds = new Set<string>();
      for (const r of rows) {
        totalMC += r.model_count;
        preds.add(r.predicate);
      }
      const mc: Record<string, number> = {};
      for (const name of activeModels) {
        mc[name] = rows.filter((r) => r.models.includes(name)).length;
      }
      result[domain] = {
        count: rows.length,
        avgModelCount: totalMC / rows.length,
        uniquePredicates: preds.size,
        modelCoverage: mc,
      };
    }
    return result;
  }, [groups, activeModels]);

  const toggleGroup = (domain: string) =>
    setExpandedGroups((prev) => ({ ...prev, [domain]: !prev[domain] }));

  const allExpanded = groups.every(([d]) => expandedGroups[d]);
  const toggleAll = () => {
    const next: Record<string, boolean> = {};
    for (const [d] of groups) next[d] = !allExpanded;
    setExpandedGroups(next);
  };

  if (propositions.length === 0) {
    return (
      <div className="text-zinc-500 text-sm py-4">No propositions extracted by any model.</div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <button
          onClick={toggleAll}
          className="text-[11px] text-cyan-400 hover:text-cyan-300 font-mono"
        >
          {allExpanded ? "Collapse All" : "Expand All"}
        </button>
        <span className="text-[11px] text-zinc-500">{propositions.length} propositions total</span>
      </div>
      <div className="overflow-auto max-h-[600px]">
        <table className="w-full text-xs font-mono border-collapse">
          <thead className="sticky top-0 bg-surface-0 z-10">
            <tr className="text-zinc-400 border-b border-white/5">
              <th className="text-left py-2 px-1 w-8" />
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
              {activeModels.map((name) => (
                <th key={name} className="text-center py-2 px-1 min-w-[50px]">
                  <span className="text-[11px]">{name.replace(/-/g, "\u200b-")}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map(([domain, rows]) => {
              const agg = aggs[domain]!;
              const isOpen = !!expandedGroups[domain];
              return (
                <PropGroupRows
                  key={domain}
                  domain={domain}
                  rows={rows}
                  agg={agg}
                  isOpen={isOpen}
                  modelNames={activeModels}
                  onToggle={() => toggleGroup(domain)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
      {hiddenModels.length > 0 && (
        <p className="text-[11px] text-zinc-500 mt-1">
          Hidden (no propositions extracted): {hiddenModels.join(", ")}
        </p>
      )}
    </div>
  );
}

function PropGroupRows({
  domain,
  rows,
  agg,
  isOpen,
  modelNames,
  onToggle,
}: {
  domain: string;
  rows: PropositionRow[];
  agg: GroupAgg;
  isOpen: boolean;
  modelNames: string[];
  onToggle: () => void;
}) {
  return (
    <>
      <tr className="bg-white/[0.03] border-b border-white/5 cursor-pointer" onClick={onToggle}>
        <td className="py-2 px-2">
          <span className="text-zinc-500 text-[11px]">{isOpen ? "▼" : "▶"}</span>
        </td>
        <td className="py-2 px-2">
          <div className="flex items-center gap-2">
            <DomainBadge domain={domain} />
            <span className="text-zinc-400 text-[11px]">
              {agg.count} proposition{agg.count !== 1 ? "s" : ""}
            </span>
          </div>
        </td>
        <td className="py-1.5 px-1 text-center">
          <span className="text-zinc-500 text-[11px]">{agg.uniquePredicates} unique</span>
        </td>
        <td className="py-1.5 px-1" />
        <td className="py-1.5 px-1 text-center">
          <span className="text-zinc-500 text-[11px]">avg {agg.avgModelCount.toFixed(1)}</span>
        </td>
        {modelNames.map((name) => {
          const found = agg.modelCoverage[name] || 0;
          const pct = agg.count > 0 ? Math.round((found / agg.count) * 100) : 0;
          return (
            <td key={name} className="py-1.5 px-1 text-center">
              <span
                className={`text-[11px] ${pct >= 80 ? "text-emerald-500" : pct >= 50 ? "text-amber-500" : "text-zinc-500"}`}
              >
                {found}/{agg.count}
              </span>
            </td>
          );
        })}
      </tr>

      {isOpen &&
        rows.map((prop, i) => (
          <tr key={i} className="border-b border-white/5 hover:bg-white/[0.02]">
            <td />
            <td className="py-1.5 px-2 text-left">
              <span className="text-zinc-200 max-w-[150px] truncate block">{prop.subject}</span>
            </td>
            <td className="py-1.5 px-1 text-left">
              <span className="text-cyan-400">{prop.predicate}</span>
            </td>
            <td className="py-1.5 px-1 text-left">
              <span className="text-zinc-200 max-w-[150px] truncate block">{prop.object}</span>
            </td>
            <td className="py-1.5 px-1 text-center text-zinc-400">{prop.model_count}</td>
            {modelNames.map((name) => {
              const found = prop.models.includes(name);
              return (
                <td key={name} className="py-1.5 px-1 text-center">
                  <span className={found ? "text-emerald-400" : "text-zinc-700"}>
                    {found ? "✓" : "·"}
                  </span>
                </td>
              );
            })}
          </tr>
        ))}
    </>
  );
}
