import { Fragment, useMemo, useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ChevronDown, ChevronUp, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface JsonlTableProps {
  rows: Record<string, unknown>[];
}

const MAX_PREVIEW_LEN = 80;

function previewCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string")
    return v.length > MAX_PREVIEW_LEN ? v.slice(0, MAX_PREVIEW_LEN) + "…" : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    const json = JSON.stringify(v);
    return json.length > MAX_PREVIEW_LEN ? json.slice(0, MAX_PREVIEW_LEN) + "…" : json;
  } catch {
    return String(v);
  }
}

/** Tabular preview for JSONL pipeline outputs.
 *
 *  Builds columns from the union of keys across the visible rows so partial
 *  schemas (e.g. some rows missing optional fields) still render every
 *  column without crashing. Click a row to expand into raw JSON.
 */
export function JsonlTable({ rows }: JsonlTableProps) {
  const columns = useMemo<ColumnDef<Record<string, unknown>>[]>(() => {
    const keys = new Set<string>();
    for (const row of rows.slice(0, 200)) {
      for (const k of Object.keys(row)) keys.add(k);
    }
    return Array.from(keys).map((key) => ({
      id: key,
      header: key,
      accessorFn: (row: Record<string, unknown>) => row[key],
      cell: ({ getValue }) => (
        <span className="font-mono text-[11px] text-zinc-300 truncate block max-w-[280px]">
          {previewCell(getValue())}
        </span>
      ),
    }));
  }, [rows]);

  const [sorting, setSorting] = useState<SortingState>([]);
  const [expanded, setExpanded] = useState<number | null>(null);

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (rows.length === 0) {
    return <div className="p-4 text-sm text-zinc-600">No rows</div>;
  }

  return (
    <div className="overflow-auto h-full">
      <table className="text-xs w-max min-w-full border-collapse">
        <thead className="sticky top-0 bg-surface-1 border-b border-white/10 z-10">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((header) => {
                const sortDir = header.column.getIsSorted();
                return (
                  <th
                    key={header.id}
                    onClick={header.column.getToggleSortingHandler()}
                    className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-wider text-zinc-500 hover:text-zinc-300 cursor-pointer whitespace-nowrap"
                  >
                    <span className="flex items-center gap-1">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sortDir === "asc" ? (
                        <ChevronUp className="h-3 w-3" />
                      ) : sortDir === "desc" ? (
                        <ChevronDown className="h-3 w-3" />
                      ) : (
                        <ChevronsUpDown className="h-3 w-3 opacity-30" />
                      )}
                    </span>
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row, i) => (
            <Fragment key={row.id}>
              <tr
                onClick={() => setExpanded(expanded === i ? null : i)}
                className={cn(
                  "border-b border-white/[0.03] cursor-pointer transition-colors",
                  expanded === i ? "bg-white/[0.06]" : "hover:bg-white/[0.03]",
                )}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-1.5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
              {expanded === i && (
                <tr className="bg-surface-1">
                  <td colSpan={columns.length} className="px-3 py-2">
                    <pre className="text-[11px] font-mono text-zinc-300 whitespace-pre-wrap break-all leading-relaxed">
                      {JSON.stringify(row.original, null, 2)}
                    </pre>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
