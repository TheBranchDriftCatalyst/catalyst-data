import { useState, useMemo } from "react";
import type { ModelResult } from "@/types/benchmark";
import { MetricLabel, ModelTypeBadge, METRIC_TOOLTIPS, MODEL_TYPE_ORDER } from "./shared";

interface Row {
  name: string;
  type: string;
  mentions: number;
  assertions: number;
  duration: number;
  tokensPerSec: number;
  retries: number;
  errors: number;
}

function toRows(models: ModelResult[]): Row[] {
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

function avg(rows: Row[], key: keyof Row): number {
  return rows.reduce((s, r) => s + (r[key] as number), 0) / rows.length;
}
function sum(rows: Row[], key: keyof Row): number {
  return rows.reduce((s, r) => s + (r[key] as number), 0);
}

export function ModelStatsTable({ models }: { models: ModelResult[] }) {
  const rows = useMemo(() => toRows(models), [models]);
  const [sortCol, setSortCol] = useState<keyof Row>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
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
      setSortDir("asc");
    }
  };

  const icon = (col: keyof Row) => (sortCol === col ? (sortDir === "asc" ? " ↑" : " ↓") : "");

  const cols: { key: keyof Row; label: string; tooltip?: string }[] = [
    { key: "mentions", label: "Mentions", tooltip: METRIC_TOOLTIPS.mentions },
    { key: "assertions", label: "Assertions", tooltip: METRIC_TOOLTIPS.assertions },
    { key: "duration", label: "Time(s)", tooltip: METRIC_TOOLTIPS.duration },
    { key: "tokensPerSec", label: "Tok/s", tooltip: METRIC_TOOLTIPS.tokens_per_sec },
    { key: "retries", label: "Retries", tooltip: METRIC_TOOLTIPS.retries },
    { key: "errors", label: "Errors", tooltip: METRIC_TOOLTIPS.errors },
  ];

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
            {cols.map((c) => (
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
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[9px]">
                    avg {avg(group, "mentions").toFixed(0)}
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[9px]">
                    avg {avg(group, "assertions").toFixed(0)}
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[9px]">
                    avg {avg(group, "duration").toFixed(1)}s
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[9px]">
                    avg {avg(group, "tokensPerSec").toFixed(0)}
                  </td>
                  <AggCell value={sum(group, "retries")} label="Σ" warn />
                  <AggCell value={sum(group, "errors")} label="Σ" error />
                </tr>
                {!isCollapsed &&
                  sortedGroup(group).map((r) => (
                    <tr key={r.name} className="border-b border-white/5 hover:bg-white/[0.02]">
                      <td />
                      <td className="py-1.5 px-2 text-left text-zinc-200">{r.name}</td>
                      <td className="py-1.5 px-2 text-center text-zinc-300">{r.mentions}</td>
                      <td className="py-1.5 px-2 text-center text-zinc-300">{r.assertions}</td>
                      <td className="py-1.5 px-2 text-center text-zinc-300">
                        {r.duration.toFixed(1)}
                      </td>
                      <td className="py-1.5 px-2 text-center text-zinc-300">
                        {r.tokensPerSec.toFixed(0)}
                      </td>
                      <td
                        className={`py-1.5 px-2 text-center ${r.retries > 0 ? "text-amber-400" : "text-zinc-300"}`}
                      >
                        {r.retries}
                      </td>
                      <td
                        className={`py-1.5 px-2 text-center ${r.errors > 0 ? "text-red-400" : "text-zinc-300"}`}
                      >
                        {r.errors}
                      </td>
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

function AggCell({
  value,
  label,
  warn,
  error,
}: {
  value: number;
  label: string;
  warn?: boolean;
  error?: boolean;
}) {
  const color =
    value > 0
      ? error
        ? "text-red-500"
        : warn
          ? "text-amber-500"
          : "text-zinc-500"
      : "text-zinc-500";
  return (
    <td className={`py-1.5 px-2 text-center text-[9px] ${color}`}>
      {label}
      {value}
    </td>
  );
}

function GroupBlock({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
