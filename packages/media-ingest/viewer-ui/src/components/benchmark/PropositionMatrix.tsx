import { useState, useMemo, useCallback } from "react";
import type { PropositionRow } from "@/types/benchmark";
import { DomainBadge, SortableHeader, METRIC_TOOLTIPS } from "./shared";

const DOMAIN_ORDER: Record<string, number> = {
  media: 0,
  congress: 1,
  open_leaks: 2,
  unknown: 3,
};

type SortKey = "subject" | "predicate" | "object" | "domain" | "model_count";

function compareRows(
  a: PropositionRow,
  b: PropositionRow,
  key: SortKey,
  dir: "asc" | "desc",
): number {
  let cmp: number;
  switch (key) {
    case "subject":
      cmp = a.subject.localeCompare(b.subject);
      break;
    case "predicate":
      cmp = a.predicate.localeCompare(b.predicate);
      break;
    case "object":
      cmp = a.object.localeCompare(b.object);
      break;
    case "domain":
      cmp =
        (DOMAIN_ORDER[a.domain || "unknown"] ?? 99) - (DOMAIN_ORDER[b.domain || "unknown"] ?? 99);
      break;
    case "model_count":
      cmp = a.model_count - b.model_count;
      break;
    default:
      cmp = 0;
  }
  return dir === "asc" ? cmp : -cmp;
}

export function PropositionMatrix({
  propositions,
  modelNames,
}: {
  propositions: PropositionRow[];
  modelNames: string[];
}) {
  const [sortKey, setSortKey] = useState<SortKey>("subject");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [showAll, setShowAll] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const onSort = useCallback(
    (key: string) => {
      if (key === sortKey) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortKey(key as SortKey);
        setSortDir("asc");
      }
    },
    [sortKey],
  );

  // Group by domain, sort within groups
  const grouped = useMemo(() => {
    const groups = new Map<string, PropositionRow[]>();
    for (const row of propositions) {
      const d = row.domain || "unknown";
      const list = groups.get(d) || [];
      list.push(row);
      groups.set(d, list);
    }
    for (const [, rows] of groups) {
      rows.sort((a, b) => compareRows(a, b, sortKey, sortDir));
    }
    const sortedKeys = Array.from(groups.keys()).sort(
      (a, b) => (DOMAIN_ORDER[a] ?? 99) - (DOMAIN_ORDER[b] ?? 99),
    );
    return sortedKeys.map((key) => ({ domain: key, rows: groups.get(key)! }));
  }, [propositions, sortKey, sortDir]);

  // Flatten for row limiting
  const allFlatRows = useMemo(() => {
    const flat: Array<
      | { kind: "group"; domain: string; count: number }
      | { kind: "row"; proposition: PropositionRow }
    > = [];
    for (const g of grouped) {
      flat.push({ kind: "group", domain: g.domain, count: g.rows.length });
      if (!collapsed[g.domain]) {
        for (const row of g.rows) {
          flat.push({ kind: "row", proposition: row });
        }
      }
    }
    return flat;
  }, [grouped, collapsed]);

  const visibleRows = showAll ? allFlatRows : allFlatRows.slice(0, 50);
  const totalDataRows = propositions.length;
  const colCount = 5 + modelNames.length;

  const toggleGroup = (domain: string) => {
    setCollapsed((prev) => ({ ...prev, [domain]: !prev[domain] }));
  };

  if (propositions.length === 0) {
    return (
      <div className="text-zinc-500 text-sm py-4">No propositions extracted by any model.</div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            <SortableHeader
              label="Subject"
              sortKey="subject"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              tooltip={METRIC_TOOLTIPS.subject}
              className="text-left py-2 px-1"
            />
            <SortableHeader
              label="Predicate"
              sortKey="predicate"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              tooltip={METRIC_TOOLTIPS.predicate}
              className="text-left py-2 px-1"
            />
            <SortableHeader
              label="Object"
              sortKey="object"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              tooltip={METRIC_TOOLTIPS.object}
              className="text-left py-2 px-1"
            />
            <SortableHeader
              label="Domain"
              sortKey="domain"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              tooltip={METRIC_TOOLTIPS.entity_domain}
              className="text-center py-2 px-1"
            />
            <SortableHeader
              label="#"
              sortKey="model_count"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              tooltip={METRIC_TOOLTIPS.prop_model_count}
              className="text-center py-2 px-1"
            />
            {modelNames.map((name) => (
              <th key={name} className="text-center py-2 px-1 min-w-[60px]">
                <span className="text-[10px]">{name.replace(/-/g, "\u200b-")}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((item, idx) => {
            if (item.kind === "group") {
              return (
                <tr
                  key={`group-${item.domain}`}
                  className="bg-white/[0.03] border-b border-white/5 cursor-pointer"
                  onClick={() => toggleGroup(item.domain)}
                >
                  <td colSpan={colCount} className="py-2 px-2">
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-500 text-[10px]">
                        {collapsed[item.domain] ? "▶" : "▼"}
                      </span>
                      <DomainBadge domain={item.domain} />
                      <span className="text-zinc-500 text-[10px]">
                        ({item.count} proposition{item.count !== 1 ? "s" : ""})
                      </span>
                    </div>
                  </td>
                </tr>
              );
            }

            const prop = item.proposition;
            return (
              <tr
                key={`${prop.subject}-${prop.predicate}-${idx}`}
                className="border-b border-white/5 hover:bg-white/[0.02]"
              >
                <td className="py-1.5 px-2 text-left">
                  <span className="text-zinc-200 max-w-[150px] truncate block">{prop.subject}</span>
                </td>
                <td className="py-1.5 px-2 text-left">
                  <span className="text-cyan-400">{prop.predicate}</span>
                </td>
                <td className="py-1.5 px-2 text-left">
                  <span className="text-zinc-200 max-w-[150px] truncate block">{prop.object}</span>
                </td>
                <td className="py-1.5 px-1 text-center">
                  <DomainBadge domain={prop.domain || "unknown"} />
                </td>
                <td className="py-1.5 px-1 text-center">
                  <span className="text-zinc-400">{prop.model_count}</span>
                </td>
                {modelNames.map((name) => {
                  const found = prop.models.includes(name);
                  return (
                    <td key={name} className="py-1.5 px-1 text-center">
                      <span className={found ? "text-emerald-400" : "text-zinc-700"}>
                        {found ? "✓" : "·"}
                      </span>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {!showAll && totalDataRows > 50 && (
        <button
          onClick={() => setShowAll(true)}
          className="mt-2 text-xs text-cyan-400 hover:text-cyan-300 font-mono"
        >
          Show all {totalDataRows} rows ({totalDataRows - 50} hidden)
        </button>
      )}
    </div>
  );
}
