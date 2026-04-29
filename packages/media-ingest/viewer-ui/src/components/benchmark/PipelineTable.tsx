import { useState, useMemo } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getGroupedRowModel,
  getExpandedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
  type ColumnDef,
  type ExpandedState,
} from "@tanstack/react-table";
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

  // Discover stage names from the data (supports both v1 and v2 naming)
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

  const [sorting, setSorting] = useState<SortingState>([]);
  const [expanded, setExpanded] = useState<ExpandedState>(true);

  const columns = useMemo<ColumnDef<PipelineRow, unknown>[]>(() => {
    const helper = createColumnHelper<PipelineRow>();

    const base: ColumnDef<PipelineRow, unknown>[] = [
      helper.accessor("name", {
        header: "Model",
        cell: (info) => <span className="text-zinc-200">{info.getValue()}</span>,
        enableGrouping: false,
      }) as ColumnDef<PipelineRow, unknown>,
      helper.accessor("type", {
        header: "Type",
        cell: (info) => <ModelTypeBadge type={info.getValue()} />,
        sortingFn: (rowA, rowB) => {
          const a = MODEL_TYPE_ORDER[rowA.original.type] ?? 99;
          const b = MODEL_TYPE_ORDER[rowB.original.type] ?? 99;
          return a - b;
        },
      }) as ColumnDef<PipelineRow, unknown>,
    ];

    const stageCols = stageKeys.map((key) => {
      const label = STAGE_LABELS[key] || key.replace(/_/g, " ");
      return helper.accessor((row) => row.stages[key]?.calls ?? 0, {
        id: `stage_${key}`,
        header: label,
        cell: ({ row }) => <PipelineCell info={row.original.stages[key]} />,
        meta: { tooltip: METRIC_TOOLTIPS[key] || `Pipeline stage: ${label}` },
        enableGrouping: false,
      }) as ColumnDef<PipelineRow, unknown>;
    });

    return [...base, ...stageCols];
  }, [stageKeys]);

  const table = useReactTable({
    data,
    columns,
    state: {
      sorting,
      grouping: ["type"],
      expanded,
    },
    onSortingChange: setSorting,
    onExpandedChange: setExpanded,
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (modelsWithPipeline.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id} className="text-zinc-500 border-b border-white/5">
              {headerGroup.headers.map((header) => {
                if (header.isPlaceholder) return <th key={header.id} />;
                const meta = header.column.columnDef.meta as { tooltip?: string } | undefined;
                const tooltip = meta?.tooltip;
                const label = flexRender(header.column.columnDef.header, header.getContext());

                if (header.column.getIsGrouped()) {
                  return (
                    <th key={header.id} className="text-left py-2 px-2">
                      {tooltip ? <MetricLabel label={String(label)} tooltip={tooltip} /> : label}
                    </th>
                  );
                }

                return (
                  <SortableHeader
                    key={header.id}
                    column={header.column}
                    className={`py-2 px-1 ${header.column.id === "name" ? "text-left px-2" : "text-center min-w-[70px]"}`}
                  >
                    <span className="text-[10px]">
                      {tooltip ? <MetricLabel label={String(label)} tooltip={tooltip} /> : label}
                    </span>
                  </SortableHeader>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            if (row.getIsGrouped()) {
              return (
                <tr
                  key={row.id}
                  className="bg-white/[0.03] border-b border-white/5 cursor-pointer"
                  onClick={row.getToggleExpandedHandler()}
                >
                  <td colSpan={columns.length} className="py-2 px-2">
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-500 text-[10px]">
                        {row.getIsExpanded() ? "▼" : "▶"}
                      </span>
                      <ModelTypeBadge type={row.groupingValue as string} />
                      <span className="text-zinc-500 text-[10px]">
                        ({row.subRows.length} model{row.subRows.length !== 1 ? "s" : ""})
                      </span>
                    </div>
                  </td>
                </tr>
              );
            }

            return (
              <tr key={row.id} className="border-b border-white/5 hover:bg-white/[0.02]">
                {row.getVisibleCells().map((cell) => {
                  if (cell.getIsGrouped() || cell.getIsPlaceholder()) return null;
                  return (
                    <td
                      key={cell.id}
                      className={`py-1.5 px-1 ${cell.column.id === "name" ? "text-left px-2" : "text-center"}`}
                    >
                      {cell.getIsAggregated()
                        ? null
                        : flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
