import { useState, useRef, useEffect } from "react";
import type { ModelResult } from "@/types/benchmark";
import type { GroupByDimension } from "./shared";
import { GROUP_BY_OPTIONS, MODEL_TYPE_COLORS } from "./shared";

interface TableControlsProps {
  models: ModelResult[];
  groupBy: GroupByDimension;
  onGroupByChange: (dim: GroupByDimension) => void;
  hiddenModels: Set<string>;
  onHiddenModelsChange: (hidden: Set<string>) => void;
}

export function TableControls({
  models,
  groupBy,
  onGroupByChange,
  hiddenModels,
  onHiddenModelsChange,
}: TableControlsProps) {
  const [filterOpen, setFilterOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!filterOpen) return;
    const handler = (e: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) setFilterOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [filterOpen]);

  const visibleCount = models.length - hiddenModels.size;

  const toggleModel = (name: string) => {
    const next = new Set(hiddenModels);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onHiddenModelsChange(next);
  };

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-1.5">
        <label className="text-[11px] text-zinc-500 font-mono uppercase" htmlFor="group-by-select">
          Group by
        </label>
        <select
          id="group-by-select"
          value={groupBy}
          onChange={(e) => onGroupByChange(e.target.value as GroupByDimension)}
          aria-label="Select grouping dimension"
          className="bg-surface-1 border border-white/10 rounded px-2 py-1 text-xs font-mono text-zinc-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
        >
          {GROUP_BY_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      <div className="relative" ref={filterRef}>
        <button
          onClick={() => setFilterOpen((p) => !p)}
          aria-expanded={filterOpen}
          aria-haspopup="true"
          className={`text-xs font-mono px-2 py-1 rounded border transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 ${
            hiddenModels.size > 0
              ? "bg-amber-500/10 border-amber-500/30 text-amber-300"
              : "bg-surface-1 border-white/10 text-zinc-300"
          }`}
        >
          Models: {visibleCount}/{models.length}
        </button>

        {filterOpen && (
          <div className="absolute top-full left-0 mt-1 z-20 bg-surface-1 border border-white/10 rounded-lg shadow-xl p-2 min-w-[220px]">
            <div className="flex gap-2 mb-2 border-b border-white/5 pb-2">
              <button
                onClick={() => onHiddenModelsChange(new Set())}
                className="text-[11px] text-cyan-400 hover:text-cyan-300 font-mono"
              >
                Show all
              </button>
              <button
                onClick={() => onHiddenModelsChange(new Set(models.map((m) => m.name)))}
                className="text-[11px] text-cyan-400 hover:text-cyan-300 font-mono"
              >
                Hide all
              </button>
            </div>
            <div className="space-y-0.5 max-h-[300px] overflow-y-auto">
              {models.map((m) => (
                <label
                  key={m.name}
                  className="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-white/5 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={!hiddenModels.has(m.name)}
                    onChange={() => toggleModel(m.name)}
                    className="accent-cyan-400"
                  />
                  <span className="text-xs text-zinc-200 font-mono">{m.name}</span>
                  <span
                    className={`text-[10px] ml-auto px-1 rounded ${MODEL_TYPE_COLORS[m.type] || ""}`}
                  >
                    {m.type}
                  </span>
                </label>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
