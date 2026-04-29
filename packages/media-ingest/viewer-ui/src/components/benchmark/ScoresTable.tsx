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

const columnHelper = createColumnHelper<ScoresRow>();

const scoreCell = () => ({
  cell: (info: { getValue: () => unknown }) => <ScoreCell value={info.getValue() as number} />,
});

const scoreCellBold = () => ({
  cell: (info: { getValue: () => unknown }) => (
    <span className="font-bold">
      <ScoreCell value={info.getValue() as number} />
    </span>
  ),
});

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
  columnHelper.accessor("mention_strict_precision", {
    header: "M Strict P",
    ...scoreCell(),
    meta: { tooltip: METRIC_TOOLTIPS.strict_precision },
  }),
  columnHelper.accessor("mention_strict_recall", {
    header: "M Strict R",
    ...scoreCell(),
    meta: { tooltip: METRIC_TOOLTIPS.strict_recall },
  }),
  columnHelper.accessor("mention_strict_f1", {
    header: "M Strict F1",
    ...scoreCellBold(),
    meta: { tooltip: METRIC_TOOLTIPS.strict_f1 },
  }),
  columnHelper.accessor("mention_relaxed_precision", {
    header: "M Relax P",
    ...scoreCell(),
    meta: { tooltip: METRIC_TOOLTIPS.relaxed_precision },
  }),
  columnHelper.accessor("mention_relaxed_recall", {
    header: "M Relax R",
    ...scoreCell(),
    meta: { tooltip: METRIC_TOOLTIPS.relaxed_recall },
  }),
  columnHelper.accessor("mention_relaxed_f1", {
    header: "M Relax F1",
    ...scoreCellBold(),
    meta: { tooltip: METRIC_TOOLTIPS.relaxed_f1 },
  }),
  columnHelper.accessor("mention_type_accuracy", {
    header: "Type Acc",
    ...scoreCell(),
    meta: { tooltip: METRIC_TOOLTIPS.type_accuracy },
  }),
  columnHelper.accessor("proposition_strict_f1", {
    header: "P Strict F1",
    ...scoreCellBold(),
    meta: { tooltip: METRIC_TOOLTIPS.proposition_strict_f1 },
  }),
  columnHelper.accessor("proposition_relaxed_f1", {
    header: "P Relax F1",
    ...scoreCellBold(),
    meta: { tooltip: METRIC_TOOLTIPS.proposition_relaxed_f1 },
  }),
];

export function ScoresTable({ models }: { models: ModelResult[] }) {
  const data = useMemo(() => toRows(models), [models]);
  const [sorting, setSorting] = useState<SortingState>([{ id: "mention_strict_f1", desc: true }]);
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
