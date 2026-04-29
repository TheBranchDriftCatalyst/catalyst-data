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
  type ExpandedState,
} from "@tanstack/react-table";
import type { ModelResult } from "@/types/benchmark";
import {
  MetricLabel,
  ModelTypeBadge,
  SortableHeader,
  METRIC_TOOLTIPS,
  MODEL_TYPE_ORDER,
} from "./shared";

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

const columnHelper = createColumnHelper<ModelStatsRow>();

const columns = [
  columnHelper.accessor("name", {
    header: "Model",
    cell: (info) => <span className="text-zinc-200">{info.getValue()}</span>,
    enableGrouping: false,
  }),
  columnHelper.accessor("type", {
    header: "Type",
    cell: (info) => <ModelTypeBadge type={info.getValue()} />,
    sortingFn: (rowA, rowB) => {
      const a = MODEL_TYPE_ORDER[rowA.original.type] ?? 99;
      const b = MODEL_TYPE_ORDER[rowB.original.type] ?? 99;
      return a - b;
    },
  }),
  columnHelper.accessor("mentions", {
    header: "Mentions",
    cell: (info) => <span className="text-zinc-300">{info.getValue()}</span>,
    meta: { tooltip: METRIC_TOOLTIPS.mentions },
  }),
  columnHelper.accessor("assertions", {
    header: "Assertions",
    cell: (info) => <span className="text-zinc-300">{info.getValue()}</span>,
    meta: { tooltip: METRIC_TOOLTIPS.assertions },
  }),
  columnHelper.accessor("duration", {
    header: "Time(s)",
    cell: (info) => <span className="text-zinc-300">{info.getValue().toFixed(1)}</span>,
    meta: { tooltip: METRIC_TOOLTIPS.duration },
  }),
  columnHelper.accessor("tokensPerSec", {
    header: "Tok/s",
    cell: (info) => <span className="text-zinc-300">{info.getValue().toFixed(0)}</span>,
    meta: { tooltip: METRIC_TOOLTIPS.tokens_per_sec },
  }),
  columnHelper.accessor("retries", {
    header: "Retries",
    cell: (info) => {
      const v = info.getValue();
      return <span className={v > 0 ? "text-amber-400" : "text-zinc-300"}>{v}</span>;
    },
    meta: { tooltip: METRIC_TOOLTIPS.retries },
  }),
  columnHelper.accessor("errors", {
    header: "Errors",
    cell: (info) => {
      const v = info.getValue();
      return <span className={v > 0 ? "text-red-400" : "text-zinc-300"}>{v}</span>;
    },
    meta: { tooltip: METRIC_TOOLTIPS.errors },
  }),
];

export function ModelStatsTable({ models }: { models: ModelResult[] }) {
  const data = useMemo(() => toRows(models), [models]);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [expanded, setExpanded] = useState<ExpandedState>(true);

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

                // Type column is the grouping column -- not sortable in header
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
                    className={`py-2 px-2 ${header.column.id === "name" ? "text-left" : "text-center"}`}
                  >
                    {tooltip ? <MetricLabel label={String(label)} tooltip={tooltip} /> : label}
                  </SortableHeader>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            // Group header row
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
                  // Skip the grouped column's placeholder cell
                  if (cell.getIsGrouped() || cell.getIsPlaceholder()) return null;

                  return (
                    <td
                      key={cell.id}
                      className={`py-1.5 px-2 ${cell.column.id === "name" ? "text-left" : "text-center"}`}
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
