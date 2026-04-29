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
import type { PropositionRow } from "@/types/benchmark";
import { MetricLabel, DomainBadge, SortableHeader, METRIC_TOOLTIPS } from "./shared";

const DOMAIN_ORDER: Record<string, number> = {
  media: 0,
  congress: 1,
  open_leaks: 2,
  unknown: 3,
};

export function PropositionMatrix({
  propositions,
  modelNames,
}: {
  propositions: PropositionRow[];
  modelNames: string[];
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [expanded, setExpanded] = useState<ExpandedState>(true);

  // Only show first 40 rows (as in original)
  const data = useMemo(() => propositions.slice(0, 40), [propositions]);

  const columns = useMemo<ColumnDef<PropositionRow, unknown>[]>(() => {
    const helper = createColumnHelper<PropositionRow>();

    const base: ColumnDef<PropositionRow, unknown>[] = [
      helper.accessor("subject", {
        header: "Subject",
        cell: (info) => (
          <span className="text-zinc-200 max-w-[150px] truncate block">{info.getValue()}</span>
        ),
        enableGrouping: false,
        meta: { tooltip: METRIC_TOOLTIPS.subject },
      }) as ColumnDef<PropositionRow, unknown>,
      helper.accessor("predicate", {
        header: "Predicate",
        cell: (info) => <span className="text-cyan-400">{info.getValue()}</span>,
        enableGrouping: false,
        meta: { tooltip: METRIC_TOOLTIPS.predicate },
      }) as ColumnDef<PropositionRow, unknown>,
      helper.accessor("object", {
        header: "Object",
        cell: (info) => (
          <span className="text-zinc-200 max-w-[150px] truncate block">{info.getValue()}</span>
        ),
        enableGrouping: false,
        meta: { tooltip: METRIC_TOOLTIPS.object },
      }) as ColumnDef<PropositionRow, unknown>,
      helper.accessor("domain", {
        header: "Domain",
        cell: (info) => <DomainBadge domain={info.getValue() || "unknown"} />,
        sortingFn: (rowA, rowB) => {
          const a = DOMAIN_ORDER[rowA.original.domain || "unknown"] ?? 99;
          const b = DOMAIN_ORDER[rowB.original.domain || "unknown"] ?? 99;
          return a - b;
        },
        meta: { tooltip: METRIC_TOOLTIPS.entity_domain },
      }) as ColumnDef<PropositionRow, unknown>,
      helper.accessor("model_count", {
        header: "#",
        cell: (info) => <span className="text-zinc-400">{info.getValue()}</span>,
        enableGrouping: false,
        meta: { tooltip: METRIC_TOOLTIPS.prop_model_count },
      }) as ColumnDef<PropositionRow, unknown>,
    ];

    const modelCols = modelNames.map(
      (name) =>
        helper.display({
          id: `model_${name}`,
          header: () => <span className="text-[10px]">{name.replace(/-/g, "\u200b-")}</span>,
          cell: ({ row }) => {
            const found = row.original.models.includes(name);
            return (
              <span className={found ? "text-emerald-400" : "text-zinc-700"}>
                {found ? "✓" : "·"}
              </span>
            );
          },
          enableSorting: false,
        }) as ColumnDef<PropositionRow, unknown>,
    );

    return [...base, ...modelCols];
  }, [modelNames]);

  const table = useReactTable({
    data,
    columns,
    state: {
      sorting,
      grouping: ["domain"],
      expanded,
    },
    onSortingChange: setSorting,
    onExpandedChange: setExpanded,
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (propositions.length === 0) {
    return (
      <div className="text-zinc-500 text-sm py-4">No propositions extracted by any model.</div>
    );
  }

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
                    <th key={header.id} className="text-left py-2 px-1">
                      {tooltip ? <MetricLabel label={String(label)} tooltip={tooltip} /> : label}
                    </th>
                  );
                }

                if (!header.column.getCanSort()) {
                  return (
                    <th key={header.id} className="text-center py-2 px-1 min-w-[60px]">
                      {label}
                    </th>
                  );
                }

                return (
                  <SortableHeader
                    key={header.id}
                    column={header.column}
                    className={`py-2 px-1 ${
                      ["subject", "predicate", "object"].includes(header.column.id)
                        ? "text-left"
                        : "text-center"
                    }`}
                  >
                    {tooltip ? <MetricLabel label={String(label)} tooltip={tooltip} /> : label}
                  </SortableHeader>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table
            .getRowModel()
            .rows.slice(0, 50)
            .map((row) => {
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
                        <DomainBadge domain={(row.groupingValue as string) || "unknown"} />
                        <span className="text-zinc-500 text-[10px]">
                          ({row.subRows.length} proposition{row.subRows.length !== 1 ? "s" : ""})
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
                        className={`py-1.5 px-1 ${
                          ["subject", "predicate", "object"].includes(cell.column.id)
                            ? "text-left px-2"
                            : "text-center"
                        }`}
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
