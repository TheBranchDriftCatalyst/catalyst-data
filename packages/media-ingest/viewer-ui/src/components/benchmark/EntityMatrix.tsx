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
import type { EntityRow } from "@/types/benchmark";
import { MetricLabel, TypeBadge, DomainBadge, SortableHeader, METRIC_TOOLTIPS } from "./shared";

const DOMAIN_ORDER: Record<string, number> = {
  media: 0,
  congress: 1,
  open_leaks: 2,
  unknown: 3,
};

export function EntityMatrix({
  entities,
  modelNames,
}: {
  entities: EntityRow[];
  modelNames: string[];
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [expanded, setExpanded] = useState<ExpandedState>(true);

  const columns = useMemo<ColumnDef<EntityRow, unknown>[]>(() => {
    const helper = createColumnHelper<EntityRow>();

    const base: ColumnDef<EntityRow, unknown>[] = [
      helper.accessor("text", {
        header: "Entity",
        cell: (info) => (
          <span className="text-zinc-200 max-w-[200px] truncate block">{info.getValue()}</span>
        ),
        enableGrouping: false,
        meta: { tooltip: METRIC_TOOLTIPS.entity_text, sticky: true },
      }) as ColumnDef<EntityRow, unknown>,
      helper.accessor("domain", {
        header: "Domain",
        cell: (info) => <DomainBadge domain={info.getValue() || "unknown"} />,
        sortingFn: (rowA, rowB) => {
          const a = DOMAIN_ORDER[rowA.original.domain || "unknown"] ?? 99;
          const b = DOMAIN_ORDER[rowB.original.domain || "unknown"] ?? 99;
          return a - b;
        },
        meta: { tooltip: METRIC_TOOLTIPS.entity_domain },
      }) as ColumnDef<EntityRow, unknown>,
      helper.accessor("consensus_type", {
        header: "Type",
        cell: (info) => <TypeBadge type={info.getValue()} />,
        meta: { tooltip: METRIC_TOOLTIPS.entity_type },
        enableGrouping: false,
      }) as ColumnDef<EntityRow, unknown>,
      helper.accessor("model_count", {
        header: "#",
        cell: (info) => <span className="text-zinc-400">{info.getValue()}</span>,
        meta: { tooltip: METRIC_TOOLTIPS.entity_model_count },
        enableGrouping: false,
      }) as ColumnDef<EntityRow, unknown>,
    ];

    const modelCols = modelNames.map(
      (name) =>
        helper.display({
          id: `model_${name}`,
          header: () => (
            <span className="writing-mode-vertical text-[10px]">
              {name.replace(/-/g, "\u200b-")}
            </span>
          ),
          cell: ({ row }) => {
            const entity = row.original;
            const info = entity.models[name];
            if (!info) {
              return <span className="text-zinc-700">·</span>;
            }
            const isConsensus = info.type === entity.consensus_type;
            return (
              <span
                className={isConsensus ? "text-emerald-400" : "text-amber-400"}
                title={`${info.type} (${(info.confidence * 100).toFixed(0)}%)`}
              >
                {isConsensus ? "✓" : info.type.slice(0, 3)}
              </span>
            );
          },
          enableSorting: false,
        }) as ColumnDef<EntityRow, unknown>,
    );

    return [...base, ...modelCols];
  }, [modelNames]);

  const table = useReactTable({
    data: entities,
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

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id} className="text-zinc-500 border-b border-white/5">
              {headerGroup.headers.map((header) => {
                if (header.isPlaceholder) return <th key={header.id} />;
                const meta = header.column.columnDef.meta as
                  | { tooltip?: string; sticky?: boolean }
                  | undefined;
                const tooltip = meta?.tooltip;
                const sticky = meta?.sticky;
                const label = flexRender(header.column.columnDef.header, header.getContext());

                const stickyClass = sticky ? "sticky left-0 bg-surface-0 z-10" : "";

                if (header.column.getIsGrouped()) {
                  return (
                    <th key={header.id} className={`text-left py-2 px-1 ${stickyClass}`}>
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
                    className={`py-2 px-1 ${header.column.id === "text" ? `text-left ${stickyClass}` : "text-center"}`}
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
                      <DomainBadge domain={(row.groupingValue as string) || "unknown"} />
                      <span className="text-zinc-500 text-[10px]">
                        ({row.subRows.length} entit{row.subRows.length !== 1 ? "ies" : "y"})
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
                  const meta = cell.column.columnDef.meta as { sticky?: boolean } | undefined;
                  const sticky = meta?.sticky;
                  const stickyClass = sticky ? "sticky left-0 bg-surface-0" : "";

                  return (
                    <td
                      key={cell.id}
                      className={`py-1.5 px-1 ${cell.column.id === "text" ? `text-left px-2 ${stickyClass}` : "text-center"}`}
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
