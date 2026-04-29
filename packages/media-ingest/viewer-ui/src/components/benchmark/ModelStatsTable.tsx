import { useState, useMemo, useCallback } from "react";
import type { ModelResult } from "@/types/benchmark";
import { ModelTypeBadge, SortableHeader, METRIC_TOOLTIPS, MODEL_TYPE_ORDER } from "./shared";

interface ModelStatsRow {
  name: string;
  type: string;
  mentions: number;
  assertions: number;
  duration: number;
  tokensPerSec: number;
  retries: number;
  errors: number;
}

function toRows(models: ModelResult[]): ModelStatsRow[] {
  return models.map((m) => ({
    name: m.name,
    type: m.type,
    mentions: m.stats.mention_count,
    assertions: m.stats.assertion_count,
    duration: m.stats.duration_s,
    tokensPerSec: m.stats.tokens_per_sec,
    retries: m.stats.mention_retries + m.stats.proposition_retries,
    errors: m.stats.errors,
  }));
}

type SortKey = keyof ModelStatsRow;

const COLUMNS: { key: SortKey; label: string; tooltip?: string; align: string }[] = [
  { key: "name", label: "Model", align: "text-left" },
  { key: "type", label: "Type", align: "text-left" },
  { key: "mentions", label: "Mentions", tooltip: METRIC_TOOLTIPS.mentions, align: "text-center" },
  {
    key: "assertions",
    label: "Assertions",
    tooltip: METRIC_TOOLTIPS.assertions,
    align: "text-center",
  },
  { key: "duration", label: "Time(s)", tooltip: METRIC_TOOLTIPS.duration, align: "text-center" },
  {
    key: "tokensPerSec",
    label: "Tok/s",
    tooltip: METRIC_TOOLTIPS.tokens_per_sec,
    align: "text-center",
  },
  { key: "retries", label: "Retries", tooltip: METRIC_TOOLTIPS.retries, align: "text-center" },
  { key: "errors", label: "Errors", tooltip: METRIC_TOOLTIPS.errors, align: "text-center" },
];

function compareRows(
  a: ModelStatsRow,
  b: ModelStatsRow,
  key: SortKey,
  dir: "asc" | "desc",
): number {
  let cmp: number;
  if (key === "name") {
    cmp = a.name.localeCompare(b.name);
  } else if (key === "type") {
    cmp = (MODEL_TYPE_ORDER[a.type] ?? 99) - (MODEL_TYPE_ORDER[b.type] ?? 99);
  } else {
    cmp = (a[key] as number) - (b[key] as number);
  }
  return dir === "asc" ? cmp : -cmp;
}

export function ModelStatsTable({ models }: { models: ModelResult[] }) {
  const data = useMemo(() => toRows(models), [models]);
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const onSort = useCallback(
    (key: string) => {
      if (key === sortKey) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortKey(key as SortKey);
        setSortDir("asc");
      }
    },
    [sortKey],
  );

  // Group by type, then sort within groups
  const grouped = useMemo(() => {
    const groups = new Map<string, ModelStatsRow[]>();
    for (const row of data) {
      const list = groups.get(row.type) || [];
      list.push(row);
      groups.set(row.type, list);
    }
    // Sort each group internally
    for (const [, rows] of groups) {
      rows.sort((a, b) => compareRows(a, b, sortKey, sortDir));
    }
    // Sort group keys by MODEL_TYPE_ORDER
    const sortedKeys = Array.from(groups.keys()).sort(
      (a, b) => (MODEL_TYPE_ORDER[a] ?? 99) - (MODEL_TYPE_ORDER[b] ?? 99),
    );
    return sortedKeys.map((key) => ({ type: key, rows: groups.get(key)! }));
  }, [data, sortKey, sortDir]);

  const toggleGroup = (type: string) => {
    setCollapsed((prev) => ({ ...prev, [type]: !prev[type] }));
  };

  function renderCell(row: ModelStatsRow, key: SortKey) {
    switch (key) {
      case "name":
        return <span className="text-zinc-200">{row.name}</span>;
      case "type":
        return <ModelTypeBadge type={row.type} />;
      case "duration":
        return <span className="text-zinc-300">{row.duration.toFixed(1)}</span>;
      case "tokensPerSec":
        return <span className="text-zinc-300">{row.tokensPerSec.toFixed(0)}</span>;
      case "retries":
        return (
          <span className={row.retries > 0 ? "text-amber-400" : "text-zinc-300"}>
            {row.retries}
          </span>
        );
      case "errors":
        return (
          <span className={row.errors > 0 ? "text-red-400" : "text-zinc-300"}>{row.errors}</span>
        );
      default:
        return <span className="text-zinc-300">{row[key] as number}</span>;
    }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            {COLUMNS.map((col) => (
              <SortableHeader
                key={col.key}
                label={col.label}
                sortKey={col.key}
                currentSort={sortKey}
                currentDir={sortDir}
                onSort={onSort}
                tooltip={col.tooltip}
                className={`py-2 px-2 ${col.align}`}
              />
            ))}
          </tr>
        </thead>
        <tbody>
          {grouped.map((group) => (
            <GroupRows
              key={group.type}
              group={group}
              collapsed={!!collapsed[group.type]}
              toggleGroup={toggleGroup}
              renderCell={renderCell}
              colCount={COLUMNS.length}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GroupRows({
  group,
  collapsed,
  toggleGroup,
  renderCell,
  colCount,
}: {
  group: { type: string; rows: ModelStatsRow[] };
  collapsed: boolean;
  toggleGroup: (type: string) => void;
  renderCell: (row: ModelStatsRow, key: SortKey) => React.ReactNode;
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
            {COLUMNS.map((col) => (
              <td key={col.key} className={`py-1.5 px-2 ${col.align}`}>
                {renderCell(row, col.key)}
              </td>
            ))}
          </tr>
        ))}
    </>
  );
}
