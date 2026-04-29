import { useState, useMemo, useCallback } from "react";
import type { EntityRow } from "@/types/benchmark";
import { TypeBadge, DomainBadge, SortableHeader, METRIC_TOOLTIPS } from "./shared";

const DOMAIN_ORDER: Record<string, number> = {
  media: 0,
  congress: 1,
  open_leaks: 2,
  unknown: 3,
};

type SortKey = "text" | "domain" | "consensus_type" | "model_count";

function compareRows(a: EntityRow, b: EntityRow, key: SortKey, dir: "asc" | "desc"): number {
  let cmp: number;
  switch (key) {
    case "text":
      cmp = a.text.localeCompare(b.text);
      break;
    case "domain":
      cmp =
        (DOMAIN_ORDER[a.domain || "unknown"] ?? 99) - (DOMAIN_ORDER[b.domain || "unknown"] ?? 99);
      break;
    case "consensus_type":
      cmp = a.consensus_type.localeCompare(b.consensus_type);
      break;
    case "model_count":
      cmp = a.model_count - b.model_count;
      break;
    default:
      cmp = 0;
  }
  return dir === "asc" ? cmp : -cmp;
}

export function EntityMatrix({
  entities,
  modelNames,
}: {
  entities: EntityRow[];
  modelNames: string[];
}) {
  const [sortKey, setSortKey] = useState<SortKey>("text");
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
    const groups = new Map<string, EntityRow[]>();
    for (const row of entities) {
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
  }, [entities, sortKey, sortDir]);

  // Flatten for row limiting
  const allFlatRows = useMemo(() => {
    const flat: Array<
      { kind: "group"; domain: string; count: number } | { kind: "row"; entity: EntityRow }
    > = [];
    for (const g of grouped) {
      flat.push({ kind: "group", domain: g.domain, count: g.rows.length });
      if (!collapsed[g.domain]) {
        for (const row of g.rows) {
          flat.push({ kind: "row", entity: row });
        }
      }
    }
    return flat;
  }, [grouped, collapsed]);

  const visibleRows = showAll ? allFlatRows : allFlatRows.slice(0, 50);
  const totalDataRows = entities.length;
  const colCount = 4 + modelNames.length;

  const toggleGroup = (domain: string) => {
    setCollapsed((prev) => ({ ...prev, [domain]: !prev[domain] }));
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 border-b border-white/5">
            <SortableHeader
              label="Entity"
              sortKey="text"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              tooltip={METRIC_TOOLTIPS.entity_text}
              className="text-left py-2 px-1 sticky left-0 bg-surface-0 z-10"
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
              label="Type"
              sortKey="consensus_type"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              tooltip={METRIC_TOOLTIPS.entity_type}
              className="text-center py-2 px-1"
            />
            <SortableHeader
              label="#"
              sortKey="model_count"
              currentSort={sortKey}
              currentDir={sortDir}
              onSort={onSort}
              tooltip={METRIC_TOOLTIPS.entity_model_count}
              className="text-center py-2 px-1"
            />
            {modelNames.map((name) => (
              <th key={name} className="text-center py-2 px-1 min-w-[60px]">
                <span className="writing-mode-vertical text-[10px]">
                  {name.replace(/-/g, "\u200b-")}
                </span>
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
                        ({item.count} entit{item.count !== 1 ? "ies" : "y"})
                      </span>
                    </div>
                  </td>
                </tr>
              );
            }

            const entity = item.entity;
            return (
              <tr
                key={`${entity.text}-${idx}`}
                className="border-b border-white/5 hover:bg-white/[0.02]"
              >
                <td className="py-1.5 px-2 text-left sticky left-0 bg-surface-0">
                  <span className="text-zinc-200 max-w-[200px] truncate block">{entity.text}</span>
                </td>
                <td className="py-1.5 px-1 text-center">
                  <DomainBadge domain={entity.domain || "unknown"} />
                </td>
                <td className="py-1.5 px-1 text-center">
                  <TypeBadge type={entity.consensus_type} />
                </td>
                <td className="py-1.5 px-1 text-center">
                  <span className="text-zinc-400">{entity.model_count}</span>
                </td>
                {modelNames.map((name) => {
                  const info = entity.models[name];
                  if (!info) {
                    return (
                      <td key={name} className="py-1.5 px-1 text-center">
                        <span className="text-zinc-700">·</span>
                      </td>
                    );
                  }
                  const isConsensus = info.type === entity.consensus_type;
                  return (
                    <td key={name} className="py-1.5 px-1 text-center">
                      <span
                        className={isConsensus ? "text-emerald-400" : "text-amber-400"}
                        title={`${info.type} (${(info.confidence * 100).toFixed(0)}%)`}
                      >
                        {isConsensus ? "✓" : info.type.slice(0, 3)}
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
