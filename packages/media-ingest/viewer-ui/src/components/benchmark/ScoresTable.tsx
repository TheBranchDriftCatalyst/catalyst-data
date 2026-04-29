import { useState, useMemo, useCallback } from "react";
import type { ModelResult } from "@/types/benchmark";
import {
  ModelTypeBadge,
  ScoreCell,
  SortableHeader,
  METRIC_TOOLTIPS,
  MODEL_TYPE_ORDER,
} from "./shared";

interface ScoresRow {
  name: string;
  type: string;
  mention_strict_precision: number;
  mention_strict_recall: number;
  mention_strict_f1: number;
  mention_relaxed_precision: number;
  mention_relaxed_recall: number;
  mention_relaxed_f1: number;
  mention_type_accuracy: number;
  proposition_strict_f1: number;
  proposition_relaxed_f1: number;
}

function toRows(models: ModelResult[]): ScoresRow[] {
  return models
    .filter((m) => m.scores != null)
    .map((m) => {
      const s = m.scores!;
      return {
        name: m.name,
        type: m.type,
        mention_strict_precision: s.mention_strict_precision,
        mention_strict_recall: s.mention_strict_recall,
        mention_strict_f1: s.mention_strict_f1,
        mention_relaxed_precision: s.mention_relaxed_precision,
        mention_relaxed_recall: s.mention_relaxed_recall,
        mention_relaxed_f1: s.mention_relaxed_f1,
        mention_type_accuracy: s.mention_type_accuracy,
        proposition_strict_f1: s.proposition_strict_f1,
        proposition_relaxed_f1: s.proposition_relaxed_f1,
      };
    });
}

type SortKey = keyof ScoresRow;

interface ColumnDef {
  key: SortKey;
  label: string;
  tooltip?: string;
  bold?: boolean;
}

const COLUMNS: ColumnDef[] = [
  { key: "name", label: "Model" },
  { key: "type", label: "Type" },
  {
    key: "mention_strict_precision",
    label: "M Strict P",
    tooltip: METRIC_TOOLTIPS.strict_precision,
  },
  { key: "mention_strict_recall", label: "M Strict R", tooltip: METRIC_TOOLTIPS.strict_recall },
  {
    key: "mention_strict_f1",
    label: "M Strict F1",
    tooltip: METRIC_TOOLTIPS.strict_f1,
    bold: true,
  },
  {
    key: "mention_relaxed_precision",
    label: "M Relax P",
    tooltip: METRIC_TOOLTIPS.relaxed_precision,
  },
  { key: "mention_relaxed_recall", label: "M Relax R", tooltip: METRIC_TOOLTIPS.relaxed_recall },
  {
    key: "mention_relaxed_f1",
    label: "M Relax F1",
    tooltip: METRIC_TOOLTIPS.relaxed_f1,
    bold: true,
  },
  { key: "mention_type_accuracy", label: "Type Acc", tooltip: METRIC_TOOLTIPS.type_accuracy },
  {
    key: "proposition_strict_f1",
    label: "P Strict F1",
    tooltip: METRIC_TOOLTIPS.proposition_strict_f1,
    bold: true,
  },
  {
    key: "proposition_relaxed_f1",
    label: "P Relax F1",
    tooltip: METRIC_TOOLTIPS.proposition_relaxed_f1,
    bold: true,
  },
];

function compareRows(a: ScoresRow, b: ScoresRow, key: SortKey, dir: "asc" | "desc"): number {
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

export function ScoresTable({ models }: { models: ModelResult[] }) {
  const data = useMemo(() => toRows(models), [models]);
  const [sortKey, setSortKey] = useState<SortKey>("mention_strict_f1");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
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

  const grouped = useMemo(() => {
    const groups = new Map<string, ScoresRow[]>();
    for (const row of data) {
      const list = groups.get(row.type) || [];
      list.push(row);
      groups.set(row.type, list);
    }
    for (const [, rows] of groups) {
      rows.sort((a, b) => compareRows(a, b, sortKey, sortDir));
    }
    const sortedKeys = Array.from(groups.keys()).sort(
      (a, b) => (MODEL_TYPE_ORDER[a] ?? 99) - (MODEL_TYPE_ORDER[b] ?? 99),
    );
    return sortedKeys.map((key) => ({ type: key, rows: groups.get(key)! }));
  }, [data, sortKey, sortDir]);

  const toggleGroup = (type: string) => {
    setCollapsed((prev) => ({ ...prev, [type]: !prev[type] }));
  };

  function renderCell(row: ScoresRow, col: ColumnDef) {
    if (col.key === "name") {
      return <span className="text-zinc-200">{row.name}</span>;
    }
    if (col.key === "type") {
      return <ModelTypeBadge type={row.type} />;
    }
    const value = row[col.key] as number;
    const cell = <ScoreCell value={value} />;
    return col.bold ? <span className="font-bold">{cell}</span> : cell;
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
                className={`py-2 px-2 ${col.key === "name" ? "text-left" : "text-center"}`}
              />
            ))}
          </tr>
        </thead>
        <tbody>
          {grouped.map((group) => (
            <ScoresGroupRows
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

function ScoresGroupRows({
  group,
  collapsed,
  toggleGroup,
  renderCell,
  colCount,
}: {
  group: { type: string; rows: ScoresRow[] };
  collapsed: boolean;
  toggleGroup: (type: string) => void;
  renderCell: (row: ScoresRow, col: ColumnDef) => React.ReactNode;
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
              <td
                key={col.key}
                className={`py-1.5 px-2 ${col.key === "name" ? "text-left" : "text-center"}`}
              >
                {renderCell(row, col)}
              </td>
            ))}
          </tr>
        ))}
    </>
  );
}
