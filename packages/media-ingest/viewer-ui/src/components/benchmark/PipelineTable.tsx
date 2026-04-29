import { useState, useMemo, useCallback } from "react";
import type { ModelResult, PipelineStage } from "@/types/benchmark";
import {
  MetricLabel,
  ModelTypeBadge,
  SortableHeader,
  METRIC_TOOLTIPS,
  STAGE_LABELS,
  MODEL_TYPE_ORDER,
} from "./shared";

interface PipelineRow {
  name: string;
  type: string;
  stages: Record<string, PipelineStage>;
}

function PipelineCell({ info }: { info: PipelineStage | undefined }) {
  if (!info || info.calls === 0) {
    return <span className="text-zinc-700">&mdash;</span>;
  }
  const hasErrors = info.error > 0 || info.failed > 0;
  const hasAmbiguous = (info.ambiguous || 0) > 0;
  return (
    <span
      className={hasErrors ? "text-red-400" : hasAmbiguous ? "text-amber-400" : "text-zinc-300"}
    >
      {info.calls}
      {hasErrors && <span className="text-red-500 text-[9px]">({info.error + info.failed}e)</span>}
      {hasAmbiguous && !hasErrors && (
        <span className="text-amber-500 text-[9px]">({info.ambiguous}a)</span>
      )}
    </span>
  );
}

export function PipelineTable({ models }: { models: ModelResult[] }) {
  const modelsWithPipeline = useMemo(
    () => models.filter((m) => Object.keys(m.pipeline).length > 0),
    [models],
  );

  // Discover stage names from the data
  const stageKeys = useMemo(() => {
    const set = new Set<string>();
    for (const m of modelsWithPipeline) {
      for (const key of Object.keys(m.pipeline)) {
        set.add(key);
      }
    }
    return Array.from(set).sort();
  }, [modelsWithPipeline]);

  const data = useMemo<PipelineRow[]>(
    () =>
      modelsWithPipeline.map((m) => ({
        name: m.name,
        type: m.type,
        stages: m.pipeline,
      })),
    [modelsWithPipeline],
  );

  const [sortKey, setSortKey] = useState<string>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const onSort = useCallback(
    (key: string) => {
      if (key === sortKey) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortKey(key);
        setSortDir("asc");
      }
    },
    [sortKey],
  );

  const grouped = useMemo(() => {
    const groups = new Map<string, PipelineRow[]>();
    for (const row of data) {
      const list = groups.get(row.type) || [];
      list.push(row);
      groups.set(row.type, list);
    }
    for (const [, rows] of groups) {
      rows.sort((a, b) => {
        let cmp: number;
        if (sortKey === "name") {
          cmp = a.name.localeCompare(b.name);
        } else if (sortKey === "type") {
          cmp = (MODEL_TYPE_ORDER[a.type] ?? 99) - (MODEL_TYPE_ORDER[b.type] ?? 99);
        } else {
          // Stage column: sort by calls count
          const stageKey = sortKey.replace(/^stage_/, "");
          const aVal = a.stages[stageKey]?.calls ?? 0;
          const bVal = b.stages[stageKey]?.calls ?? 0;
          cmp = aVal - bVal;
        }
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
    const sortedKeys = Array.from(groups.keys()).sort(
      (a, b) => (MODEL_TYPE_ORDER[a] ?? 99) - (MODEL_TYPE_ORDER[b] ?? 99),
    );
    return sortedKeys.map((key) => ({ type: key, rows: groups.get(key)! }));
  }, [data, sortKey, sortDir]);

  const toggleGroup = (type: string) => {
    setCollapsed((prev) => ({ ...prev, [type]: !prev[type] }));
  };

  if (modelsWithPipeline.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            <SortableHeader
              label="Model"
              sortKey="name"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              className="text-left py-2 px-2"
            />
            <SortableHeader
              label="Type"
              sortKey="type"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              className="text-left py-2 px-2"
            />
            {stageKeys.map((key) => {
              const label = STAGE_LABELS[key] || key.replace(/_/g, " ");
              const tooltip = METRIC_TOOLTIPS[key] || `Pipeline stage: ${label}`;
              return (
                <SortableHeader
                  key={key}
                  sortKey={`stage_${key}`}
                  currentSort={sortKey}
                  currentDir={sortDir}
                  onSort={onSort}
                  className="text-center py-2 px-1 min-w-[70px]"
                >
                  <span className="text-[10px]">
                    <MetricLabel label={label} tooltip={tooltip} />
                  </span>
                </SortableHeader>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {grouped.map((group) => (
            <PipelineGroupRows
              key={group.type}
              group={group}
              stageKeys={stageKeys}
              collapsed={!!collapsed[group.type]}
              toggleGroup={toggleGroup}
              colCount={2 + stageKeys.length}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PipelineGroupRows({
  group,
  stageKeys,
  collapsed,
  toggleGroup,
  colCount,
}: {
  group: { type: string; rows: PipelineRow[] };
  stageKeys: string[];
  collapsed: boolean;
  toggleGroup: (type: string) => void;
  colCount: number;
}) {
  return (
    <>
      <tr
        className="bg-white/[0.03] border-b border-white/5 cursor-pointer"
        onClick={() => toggleGroup(group.type)}
      >
        <td colSpan={colCount} className="py-2 px-2">
          <div className="flex items-center gap-2">
            <span className="text-zinc-500 text-[10px]">{collapsed ? "▶" : "▼"}</span>
            <ModelTypeBadge type={group.type} />
            <span className="text-zinc-500 text-[10px]">
              ({group.rows.length} model{group.rows.length !== 1 ? "s" : ""})
            </span>
          </div>
        </td>
      </tr>
      {!collapsed &&
        group.rows.map((row) => (
          <tr key={row.name} className="border-b border-white/5 hover:bg-white/[0.02]">
            <td className="py-1.5 px-2 text-left">
              <span className="text-zinc-200">{row.name}</span>
            </td>
            <td className="py-1.5 px-2 text-left">
              <ModelTypeBadge type={row.type} />
            </td>
            {stageKeys.map((key) => (
              <td key={key} className="py-1.5 px-1 text-center">
                <PipelineCell info={row.stages[key]} />
              </td>
            ))}
          </tr>
        ))}
    </>
  );
}
