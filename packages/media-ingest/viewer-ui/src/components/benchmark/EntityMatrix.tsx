import { useState, useMemo } from "react";
import type { EntityRow } from "@/types/benchmark";
import { DomainBadge, TypeBadge, METRIC_TOOLTIPS, MetricLabel } from "./shared";

const DOMAIN_ORDER: Record<string, number> = {
  media: 0,
  congress: 1,
  open_leaks: 2,
  unknown: 3,
};

interface GroupAgg {
  count: number;
  avgModelCount: number;
  topTypes: [string, number][];
  modelCoverage: Record<string, number>;
}

export function EntityMatrix({
  entities,
  modelNames,
  onSelectEntity,
  selectedEntityKey,
}: {
  entities: EntityRow[];
  modelNames: string[];
  onSelectEntity?: (entity: EntityRow) => void;
  selectedEntityKey?: string | null;
}) {
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // Filter out models with zero results across all entities
  const { active: activeModels, hidden: hiddenModels } = useMemo(() => {
    const active: string[] = [];
    const hidden: string[] = [];
    for (const name of modelNames) {
      const hasAny = entities.some((e) => e.models[name]);
      if (hasAny) active.push(name);
      else hidden.push(name);
    }
    return { active, hidden };
  }, [entities, modelNames]);

  // Group by domain
  const groups = useMemo(() => {
    const map: Record<string, EntityRow[]> = {};
    for (const e of entities) {
      const d = e.domain || "unknown";
      (map[d] ??= []).push(e);
    }
    return Object.entries(map).sort(
      (a, b) => (DOMAIN_ORDER[a[0]] ?? 99) - (DOMAIN_ORDER[b[0]] ?? 99),
    );
  }, [entities]);

  // Aggregates per group
  const aggs = useMemo(() => {
    const result: Record<string, GroupAgg> = {};
    for (const [domain, rows] of groups) {
      const typeCounts: Record<string, number> = {};
      let totalMC = 0;
      for (const r of rows) {
        typeCounts[r.consensus_type] = (typeCounts[r.consensus_type] || 0) + 1;
        totalMC += r.model_count;
      }
      const mc: Record<string, number> = {};
      for (const name of activeModels) {
        mc[name] = rows.filter((r) => r.models[name]).length;
      }
      result[domain] = {
        count: rows.length,
        avgModelCount: totalMC / rows.length,
        topTypes: Object.entries(typeCounts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3),
        modelCoverage: mc,
      };
    }
    return result;
  }, [groups, activeModels]);

  // Sort rows within expanded groups
  const sortedGroup = (rows: EntityRow[]) => {
    if (!sortCol) return rows;
    const sorted = [...rows];
    sorted.sort((a, b) => {
      let cmp = 0;
      if (sortCol === "text") cmp = a.text.localeCompare(b.text);
      else if (sortCol === "consensus_type") cmp = a.consensus_type.localeCompare(b.consensus_type);
      else if (sortCol === "model_count") cmp = a.model_count - b.model_count;
      return sortDir === "desc" ? -cmp : cmp;
    });
    return sorted;
  };

  const toggleSort = (col: string) => {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortCol(col);
      setSortDir("asc");
    }
  };

  const toggleGroup = (domain: string) =>
    setExpandedGroups((prev) => ({ ...prev, [domain]: !prev[domain] }));

  const allExpanded = groups.every(([d]) => expandedGroups[d]);
  const toggleAll = () => {
    const next: Record<string, boolean> = {};
    for (const [d] of groups) next[d] = !allExpanded;
    setExpandedGroups(next);
  };

  const sortIcon = (col: string) => (sortCol === col ? (sortDir === "asc" ? " ↑" : " ↓") : "");

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <button
          onClick={toggleAll}
          className="text-[11px] text-cyan-400 hover:text-cyan-300 font-mono"
        >
          {allExpanded ? "Collapse All" : "Expand All"}
        </button>
        <span className="text-[11px] text-zinc-500">{entities.length} entities total</span>
      </div>
      <div className="overflow-auto max-h-[600px]">
        <table className="w-full text-xs font-mono border-collapse">
          <thead className="sticky top-0 bg-surface-0 z-10">
            <tr className="text-zinc-400 border-b border-white/5">
              <th className="text-left py-2 px-1 w-8" />
              <th
                className="text-left py-2 px-2 cursor-pointer select-none"
                onClick={() => toggleSort("text")}
              >
                <MetricLabel label="Entity" tooltip={METRIC_TOOLTIPS.entity_text} />
                {sortIcon("text")}
              </th>
              <th
                className="text-left py-2 px-1 cursor-pointer select-none"
                onClick={() => toggleSort("consensus_type")}
              >
                <MetricLabel label="Type" tooltip={METRIC_TOOLTIPS.entity_type} />
                {sortIcon("consensus_type")}
              </th>
              <th
                className="text-center py-2 px-1 cursor-pointer select-none"
                onClick={() => toggleSort("model_count")}
              >
                <MetricLabel label="#" tooltip={METRIC_TOOLTIPS.entity_model_count} />
                {sortIcon("model_count")}
              </th>
              {activeModels.map((name) => (
                <th key={name} className="text-center py-2 px-1 min-w-[50px]">
                  <span className="writing-mode-vertical text-[11px]">
                    {name.replace(/-/g, "\u200b-")}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map(([domain, rows]) => {
              const agg = aggs[domain]!;
              const isOpen = !!expandedGroups[domain];
              return (
                <GroupRows
                  key={domain}
                  domain={domain}
                  rows={rows}
                  agg={agg}
                  isOpen={isOpen}
                  modelNames={activeModels}
                  onToggle={() => toggleGroup(domain)}
                  sortedRows={isOpen ? sortedGroup(rows) : []}
                  onSelectEntity={onSelectEntity}
                  selectedEntityKey={selectedEntityKey}
                />
              );
            })}
          </tbody>
        </table>
      </div>
      {hiddenModels.length > 0 && (
        <p className="text-[11px] text-zinc-500 mt-1">
          Hidden (no entities extracted): {hiddenModels.join(", ")}
        </p>
      )}
    </div>
  );
}

// Split into a separate component so React can bail out of rendering closed groups
function GroupRows({
  domain,
  rows: _rows,
  agg,
  isOpen,
  modelNames,
  onToggle,
  sortedRows,
  onSelectEntity,
  selectedEntityKey,
}: {
  domain: string;
  rows: EntityRow[];
  agg: GroupAgg;
  isOpen: boolean;
  modelNames: string[];
  onToggle: () => void;
  sortedRows: EntityRow[];
  onSelectEntity?: (entity: EntityRow) => void;
  selectedEntityKey?: string | null;
}) {
  return (
    <>
      {/* Group header */}
      <tr className="bg-white/[0.03] border-b border-white/5 cursor-pointer" onClick={onToggle}>
        <td className="py-2 px-2">
          <span className="text-zinc-500 text-[11px]">{isOpen ? "▼" : "▶"}</span>
        </td>
        <td className="py-2 px-2">
          <div className="flex items-center gap-2">
            <DomainBadge domain={domain} />
            <span className="text-zinc-400 text-[11px]">
              {agg.count} entit{agg.count !== 1 ? "ies" : "y"}
            </span>
          </div>
        </td>
        <td className="py-1.5 px-1 text-center">
          <div className="flex flex-wrap gap-0.5 justify-center">
            {agg.topTypes.map(([t, n]) => (
              <span key={t} className="text-[11px] text-zinc-500">
                {t}:{n}
              </span>
            ))}
          </div>
        </td>
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

      {/* Expanded entity rows.
          Per-model breakdown is shown in the EntityJsonPanel side drawer
          (click a row); we no longer render an on-hover tooltip here since
          it duplicated the drawer content and fought the row's click
          affordance. */}
      {isOpen &&
        sortedRows.map((entity) => {
          const entityKey = `${entity.domain}::${entity.consensus_type}::${entity.text}`;
          const isSelected = selectedEntityKey === entityKey;
          const isClickable = !!onSelectEntity;
          return (
            <tr
              key={entity.text}
              className={[
                "border-b border-white/5 transition-colors",
                isClickable
                  ? "cursor-pointer hover:bg-cyan-500/5"
                  : "cursor-default hover:bg-white/[0.02]",
                isSelected ? "bg-cyan-500/10 outline outline-1 outline-cyan-500/40" : "",
              ].join(" ")}
              onClick={isClickable ? () => onSelectEntity?.(entity) : undefined}
            >
              <td />
              <td className="py-1.5 px-2 text-left">
                <span className="text-zinc-200 max-w-[200px] truncate block">{entity.text}</span>
              </td>
              <td className="py-1.5 px-1 text-center">
                <TypeBadge type={entity.consensus_type} />
              </td>
              <td className="py-1.5 px-1 text-center text-zinc-400">{entity.model_count}</td>
              {modelNames.map((name) => {
                const info = entity.models[name];
                if (!info) {
                  return (
                    <td key={name} className="py-1.5 px-1 text-center text-zinc-700">
                      ·
                    </td>
                  );
                }
                const isConsensus = info.type === entity.consensus_type;
                return (
                  <td key={name} className="py-1.5 px-1 text-center">
                    <span className={isConsensus ? "text-emerald-400" : "text-amber-400"}>
                      {isConsensus ? "✓" : info.type.slice(0, 3)}
                    </span>
                  </td>
                );
              })}
            </tr>
          );
        })}
    </>
  );
}
