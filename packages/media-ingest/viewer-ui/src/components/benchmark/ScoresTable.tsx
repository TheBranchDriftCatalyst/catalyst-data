import { useState, useMemo } from "react";
import type { ModelResult } from "@/types/benchmark";
import {
  MetricLabel,
  ModelTypeBadge,
  ScoreCell,
  METRIC_TOOLTIPS,
  MODEL_TYPE_ORDER,
} from "./shared";

interface Row {
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

function toRows(models: ModelResult[]): Row[] {
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

const scoreCols: { key: keyof Row; label: string; tooltip?: string; bold?: boolean }[] = [
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

export function ScoresTable({ models }: { models: ModelResult[] }) {
  const rows = useMemo(() => toRows(models), [models]);
  const [sortCol, setSortCol] = useState<keyof Row>("mention_strict_f1");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const groups = useMemo(() => {
    const map: Record<string, Row[]> = {};
    for (const r of rows) (map[r.type] ??= []).push(r);
    return Object.entries(map).sort(
      (a, b) => (MODEL_TYPE_ORDER[a[0]] ?? 99) - (MODEL_TYPE_ORDER[b[0]] ?? 99),
    );
  }, [rows]);

  const sortedGroup = (group: Row[]) => {
    const sorted = [...group];
    sorted.sort((a, b) => {
      const av = a[sortCol],
        bv = b[sortCol];
      const cmp =
        typeof av === "string" ? av.localeCompare(bv as string) : (av as number) - (bv as number);
      return sortDir === "desc" ? -cmp : cmp;
    });
    return sorted;
  };

  const toggleSort = (col: keyof Row) => {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortCol(col);
      setSortDir("desc");
    }
  };

  const icon = (col: keyof Row) => (sortCol === col ? (sortDir === "asc" ? " ↑" : " ↓") : "");

  const groupAvg = (group: Row[], key: keyof Row) =>
    group.reduce((s, r) => s + (r[key] as number), 0) / group.length;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            <th className="w-8" />
            <th
              className="text-left py-2 px-2 cursor-pointer select-none"
              onClick={() => toggleSort("name")}
            >
              Model{icon("name")}
            </th>
            {scoreCols.map((c) => (
              <th
                key={c.key}
                className="text-center py-2 px-2 cursor-pointer select-none"
                onClick={() => toggleSort(c.key)}
              >
                {c.tooltip ? <MetricLabel label={c.label} tooltip={c.tooltip} /> : c.label}
                {icon(c.key)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map(([type, group]) => {
            const isCollapsed = !!collapsed[type];
            return (
              <GroupBlock key={type}>
                <tr
                  className="bg-white/[0.03] border-b border-white/5 cursor-pointer"
                  onClick={() => setCollapsed((p) => ({ ...p, [type]: !isCollapsed }))}
                >
                  <td className="py-2 px-2">
                    <span className="text-zinc-500 text-[10px]">{isCollapsed ? "▶" : "▼"}</span>
                  </td>
                  <td className="py-2 px-2">
                    <div className="flex items-center gap-2">
                      <ModelTypeBadge type={type} />
                      <span className="text-zinc-500 text-[10px]">
                        ({group.length} model{group.length !== 1 ? "s" : ""})
                      </span>
                    </div>
                  </td>
                  {scoreCols.map((c) => (
                    <td
                      key={c.key}
                      className={`py-1.5 px-2 text-center text-[9px] ${c.bold ? "font-bold" : ""}`}
                    >
                      <ScoreCell value={groupAvg(group, c.key)} />
                    </td>
                  ))}
                </tr>
                {!isCollapsed &&
                  sortedGroup(group).map((r) => (
                    <tr key={r.name} className="border-b border-white/5 hover:bg-white/[0.02]">
                      <td />
                      <td className="py-1.5 px-2 text-left text-zinc-200">{r.name}</td>
                      {scoreCols.map((c) => (
                        <td
                          key={c.key}
                          className={`py-1.5 px-2 text-center ${c.bold ? "font-bold" : ""}`}
                        >
                          <ScoreCell value={r[c.key] as number} />
                        </td>
                      ))}
                    </tr>
                  ))}
              </GroupBlock>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function GroupBlock({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
