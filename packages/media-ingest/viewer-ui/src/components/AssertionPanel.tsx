import { useState, useMemo } from "react";
import type { Assertion } from "@/types/media";

interface AssertionPanelProps {
  assertions: Assertion[];
  className?: string;
}

type SortField = "confidence" | "predicate" | "subject";

export default function AssertionPanel({
  assertions,
  className = "",
}: AssertionPanelProps) {
  const [sortBy, setSortBy] = useState<SortField>("confidence");
  const [sortAsc, setSortAsc] = useState(false);
  const [filterText, setFilterText] = useState("");

  const sorted = useMemo(() => {
    let filtered = assertions;
    if (filterText) {
      const q = filterText.toLowerCase();
      filtered = assertions.filter(
        (a) =>
          a.subject_text.toLowerCase().includes(q) ||
          a.predicate.toLowerCase().includes(q) ||
          a.object_text.toLowerCase().includes(q)
      );
    }

    return [...filtered].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "confidence":
          cmp = a.confidence - b.confidence;
          break;
        case "predicate":
          cmp = a.predicate_canonical.localeCompare(b.predicate_canonical);
          break;
        case "subject":
          cmp = a.subject_text.localeCompare(b.subject_text);
          break;
      }
      return sortAsc ? cmp : -cmp;
    });
  }, [assertions, sortBy, sortAsc, filterText]);

  const handleSort = (field: SortField) => {
    if (sortBy === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortBy(field);
      setSortAsc(false);
    }
  };

  const sortIcon = (field: SortField) => {
    if (sortBy !== field) return null;
    return (
      <span className="ml-1 text-[10px]">
        {sortAsc ? "\u25B2" : "\u25BC"}
      </span>
    );
  };

  if (assertions.length === 0) {
    return (
      <div className={`text-zinc-500 text-sm p-3 ${className}`}>
        No assertions extracted
      </div>
    );
  }

  return (
    <div className={`overflow-y-auto ${className}`}>
      {/* Filter */}
      <div className="p-2 border-b border-white/5">
        <input
          type="text"
          placeholder="Filter assertions..."
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          className="w-full bg-surface-2 text-sm text-zinc-300 placeholder-zinc-600 rounded-md px-3 py-1.5 border border-white/5 focus:outline-none focus:border-white/20 transition-colors"
        />
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/5 text-zinc-500">
              <th
                className="text-left px-3 py-2 font-medium cursor-pointer hover:text-zinc-300 transition-colors"
                onClick={() => handleSort("subject")}
              >
                Subject{sortIcon("subject")}
              </th>
              <th
                className="text-left px-3 py-2 font-medium cursor-pointer hover:text-zinc-300 transition-colors"
                onClick={() => handleSort("predicate")}
              >
                Predicate{sortIcon("predicate")}
              </th>
              <th className="text-left px-3 py-2 font-medium">Object</th>
              <th
                className="text-right px-3 py-2 font-medium cursor-pointer hover:text-zinc-300 transition-colors w-20"
                onClick={() => handleSort("confidence")}
              >
                Conf{sortIcon("confidence")}
              </th>
              <th className="px-3 py-2 font-medium w-24">Flags</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((assertion, i) => (
              <AssertionRow key={i} assertion={assertion} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Summary */}
      <div className="px-3 py-2 text-[10px] text-zinc-600 border-t border-white/5">
        {sorted.length} of {assertions.length} assertions
      </div>
    </div>
  );
}

function AssertionRow({ assertion }: { assertion: Assertion }) {
  const [showQualifiers, setShowQualifiers] = useState(false);
  const qualifierEntries = Object.entries(assertion.qualifiers);
  const hasQualifiers = qualifierEntries.length > 0;

  return (
    <>
      <tr
        className={`border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors ${
          hasQualifiers ? "cursor-pointer" : ""
        }`}
        onClick={() => hasQualifiers && setShowQualifiers(!showQualifiers)}
      >
        {/* Subject */}
        <td className="px-3 py-2 text-zinc-200 font-medium">
          {assertion.subject_text}
        </td>

        {/* Predicate */}
        <td className="px-3 py-2">
          <span className="text-zinc-400">{assertion.predicate}</span>
          {assertion.predicate !== assertion.predicate_canonical && (
            <span className="text-zinc-600 text-[10px] ml-1">
              ({assertion.predicate_canonical})
            </span>
          )}
        </td>

        {/* Object */}
        <td className="px-3 py-2 text-zinc-300">{assertion.object_text}</td>

        {/* Confidence */}
        <td className="px-3 py-2">
          <div className="flex items-center justify-end gap-1.5">
            <div className="w-12 h-1.5 bg-surface-2 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  assertion.confidence > 0.8
                    ? "bg-green-500"
                    : assertion.confidence > 0.5
                      ? "bg-amber-500"
                      : "bg-red-500"
                }`}
                style={{ width: `${assertion.confidence * 100}%` }}
              />
            </div>
            <span className="text-[10px] tabular-nums text-zinc-500 min-w-[28px] text-right">
              {(assertion.confidence * 100).toFixed(0)}%
            </span>
          </div>
        </td>

        {/* Flags */}
        <td className="px-3 py-2">
          <div className="flex items-center gap-1 justify-center">
            {assertion.negated && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-red-900/50 text-red-300 border border-red-800/50">
                NEG
              </span>
            )}
            {assertion.hedged && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-yellow-900/50 text-yellow-300 border border-yellow-800/50">
                HEDGED
              </span>
            )}
            {hasQualifiers && (
              <span className="text-zinc-600 text-[10px]">
                +{qualifierEntries.length}
              </span>
            )}
          </div>
        </td>
      </tr>

      {/* Qualifier expansion row */}
      {showQualifiers && hasQualifiers && (
        <tr className="bg-surface-1">
          <td colSpan={5} className="px-6 py-2">
            <div className="flex flex-wrap gap-1.5">
              {qualifierEntries.map(([key, value]) => (
                <span
                  key={key}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-surface-2 border border-white/5"
                >
                  <span className="text-zinc-500 font-medium">{key}:</span>
                  <span className="text-zinc-300">{value}</span>
                </span>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
