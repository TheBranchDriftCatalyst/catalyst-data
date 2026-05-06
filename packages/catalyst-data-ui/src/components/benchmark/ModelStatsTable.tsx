import { useState, useMemo, useEffect } from "react";
import type { ModelResult } from "@/types/benchmark";
import { MetricLabel, GroupBadge, METRIC_TOOLTIPS } from "./shared";
import type { GroupByDimension } from "./shared";
import { useModelGrouping } from "./useModelGrouping";
import { TrendSparkline } from "@/components/TrendSparkline";
import { useTrendData } from "@/hooks/useTrendData";

interface Row {
  name: string;
  type: string;
  mentions: number;
  assertions: number;
  duration: number;
  tokensPerSec: number;
  llmCalls: number;
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
    // llm_call_count was added to extraction stats in the per-mention provenance
    // refactor — older fixtures lack it; default to 0 for back-compat.
    llmCalls: (m.stats as { llm_call_count?: number }).llm_call_count ?? 0,
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

export function ModelStatsTable({
  models,
  groupBy = "type",
  selectedRunId = null,
  onJumpRun,
}: {
  models: ModelResult[];
  groupBy?: GroupByDimension;
  /** Active run id — passes through to per-row TrendSparkline so the
   *  matching dot is highlighted. ``null`` = following Latest. */
  selectedRunId?: string | null;
  /** Click handler when a sparkline dot is selected. Caller updates
   *  the report source / URL. */
  onJumpRun?: (runId: string) => void;
}) {
  const rows = useMemo(() => toRows(models), [models]);
  const [sortCol, setSortCol] = useState<keyof Row>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  useEffect(() => setCollapsed({}), [groupBy]);

  const groups = useModelGrouping(models, rows, groupBy);

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
    {
      key: "llmCalls",
      label: "LLM Calls",
      tooltip:
        "Total LLM API calls (NER + SPO + repair). Encoders show 0 — they bypass the LLM graph.",
    },
    { key: "retries", label: "Retries", tooltip: METRIC_TOOLTIPS.retries },
    { key: "errors", label: "Errors", tooltip: METRIC_TOOLTIPS.errors },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-400 border-b border-white/5">
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
            {/* Gap #8 — cross-run trend column. No sort handle: the order
             *  is fixed (last 10 runs), and a sort would just confuse the
             *  per-row sparkline anchor. */}
            <th className="text-center py-2 px-2 select-none">trend (last 10)</th>
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
                    <span className="text-zinc-500 text-[11px]">{isCollapsed ? "▶" : "▼"}</span>
                  </td>
                  <td className="py-2 px-2">
                    <div className="flex items-center gap-2">
                      <GroupBadge groupKey={type} dimension={groupBy} />
                      <span className="text-zinc-500 text-[11px]">
                        ({group.length} model{group.length !== 1 ? "s" : ""})
                      </span>
                    </div>
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[11px]">
                    avg {avg(group, "mentions").toFixed(0)}
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[11px]">
                    avg {avg(group, "assertions").toFixed(0)}
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[11px]">
                    avg {avg(group, "duration").toFixed(1)}s
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[11px]">
                    avg {avg(group, "tokensPerSec").toFixed(0)}
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500 text-[11px]">
                    Σ {sum(group, "llmCalls")}
                  </td>
                  <AggCell value={sum(group, "retries")} label="Σ" warn />
                  <AggCell value={sum(group, "errors")} label="Σ" error />
                  {/* group-row spacer for the trend column — keeps the
                   *  table grid aligned. No sparkline at the group level. */}
                  <td className="py-1.5 px-2" />
                </tr>
                {!isCollapsed &&
                  sortedGroup(group).map((r) => (
                    <tr
                      key={r.name}
                      data-testid={`leaderboard-row-${r.name}`}
                      className="border-b border-white/5 hover:bg-white/[0.02]"
                    >
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
                      <td className="py-1.5 px-2 text-center text-zinc-300">{r.llmCalls}</td>
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
                      <td className="py-1.5 px-2 text-center">
                        <LeaderboardRowTrend
                          model={r.name}
                          modelType={r.type}
                          selectedRunId={selectedRunId}
                          onJumpRun={onJumpRun}
                        />
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
    <td className={`py-1.5 px-2 text-center text-[11px] ${color}`}>
      {label}
      {value}
    </td>
  );
}

function GroupBlock({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

/** Per-row trend sparkline. Each row gets its own ``useTrendData`` call;
 *  react-query dedups the underlying ``report.json`` fetches across rows
 *  so the network sees one GET per (run, table) pair regardless of how
 *  many encoders/llms are listed.
 *
 *  Metric choice: encoders + the ensemble row use ``encoder_strict_f1``
 *  so the trend renders the QA signal data scientists care about. LLM
 *  rows use ``encoder_mention_count`` instead because mention F1 is run-
 *  level only on the ensemble row in the report — the per-LLM ``scores``
 *  block is identical to the encoders' but is the "as-if-this-llm-were-
 *  the-encoder" hypothetical, not really a per-LLM F1. Counts are the
 *  honest signal. */
function LeaderboardRowTrend({
  model,
  modelType,
  selectedRunId,
  onJumpRun,
}: {
  model: string;
  modelType: string;
  selectedRunId: string | null;
  onJumpRun?: (runId: string) => void;
}) {
  const isConsensusRow = model === "ensemble" || model === "consensus";
  const metric = isConsensusRow
    ? ("consensus_strict_f1" as const)
    : modelType === "encoder"
      ? ("encoder_strict_f1" as const)
      : ("encoder_mention_count" as const);
  const { points } = useTrendData({
    axis: "aggregate",
    metric,
    model,
  });
  return (
    <TrendSparkline
      points={points}
      metric={metric}
      currentRunId={selectedRunId}
      onSelectRun={(id) => onJumpRun?.(id)}
      trend="up-good"
    />
  );
}
