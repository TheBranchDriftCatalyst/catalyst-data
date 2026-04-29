import { useState, useMemo, useEffect } from "react";
import type { ModelResult, PipelineStage } from "@/types/benchmark";
import { MetricLabel, GroupBadge, METRIC_TOOLTIPS, STAGE_LABELS } from "./shared";
import type { GroupByDimension } from "./shared";
import { useModelGrouping } from "./useModelGrouping";

interface PipelineRow {
  name: string;
  type: string;
  stages: Record<string, PipelineStage>;
}

function PipelineCell({ info }: { info: PipelineStage | undefined }) {
  if (!info || info.calls === 0) return <span className="text-zinc-700">&mdash;</span>;
  const hasErrors = info.error > 0 || info.failed > 0;
  const hasAmbiguous = (info.ambiguous || 0) > 0;
  return (
    <span
      className={hasErrors ? "text-red-400" : hasAmbiguous ? "text-amber-400" : "text-zinc-300"}
    >
      {info.calls}
      {hasErrors && <span className="text-red-500 text-[11px]">({info.error + info.failed}e)</span>}
      {hasAmbiguous && !hasErrors && (
        <span className="text-amber-500 text-[11px]">({info.ambiguous}a)</span>
      )}
    </span>
  );
}

export function PipelineTable({
  models,
  groupBy = "type",
}: {
  models: ModelResult[];
  groupBy?: GroupByDimension;
}) {
  const modelsWithPipeline = useMemo(
    () => models.filter((m) => Object.keys(m.pipeline).length > 0),
    [models],
  );

  const stageKeys = useMemo(() => {
    const set = new Set<string>();
    for (const m of modelsWithPipeline) for (const key of Object.keys(m.pipeline)) set.add(key);
    return Array.from(set).sort();
  }, [modelsWithPipeline]);

  const data = useMemo<PipelineRow[]>(
    () => modelsWithPipeline.map((m) => ({ name: m.name, type: m.type, stages: m.pipeline })),
    [modelsWithPipeline],
  );

  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  useEffect(() => setCollapsed({}), [groupBy]);

  const groups = useModelGrouping(modelsWithPipeline, data, groupBy);

  if (modelsWithPipeline.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-400 border-b border-white/5">
            <th className="w-8" />
            <th className="text-left py-2 px-2">Model</th>
            {stageKeys.map((key) => {
              const label = STAGE_LABELS[key] || key.replace(/_/g, " ");
              return (
                <th key={key} className="text-center py-2 px-1 min-w-[70px]">
                  <span className="text-[11px]">
                    <MetricLabel
                      label={label}
                      tooltip={METRIC_TOOLTIPS[key] || `Pipeline stage: ${label}`}
                    />
                  </span>
                </th>
              );
            })}
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
                  {stageKeys.map((key) => {
                    const total = group.reduce((s, r) => s + (r.stages[key]?.calls ?? 0), 0);
                    const errors = group.reduce((s, r) => {
                      const st = r.stages[key];
                      return s + (st ? st.error + st.failed : 0);
                    }, 0);
                    if (total === 0)
                      return (
                        <td key={key} className="py-1.5 px-1 text-center text-zinc-700 text-[11px]">
                          &mdash;
                        </td>
                      );
                    return (
                      <td
                        key={key}
                        className={`py-1.5 px-1 text-center text-[11px] ${errors > 0 ? "text-red-500" : "text-zinc-500"}`}
                      >
                        Σ{total}
                        {errors > 0 && `(${errors}e)`}
                      </td>
                    );
                  })}
                </tr>
                {!isCollapsed &&
                  group.map((r) => (
                    <tr key={r.name} className="border-b border-white/5 hover:bg-white/[0.02]">
                      <td />
                      <td className="py-1.5 px-2 text-left text-zinc-200">{r.name}</td>
                      {stageKeys.map((key) => (
                        <td key={key} className="py-1.5 px-1 text-center">
                          <PipelineCell info={r.stages[key]} />
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
