import { useMemo } from "react";
import type { ModelResult } from "@/types/benchmark";
import type { GroupByDimension } from "./shared";
import { GROUP_SORT_ORDER } from "./shared";

function parseSizeB(tags: string[]): number | null {
  for (const t of tags) {
    const m = t.match(/^(\d+(?:\.\d+)?)(m|b)$/i);
    if (m) {
      const val = parseFloat(m[1]!);
      return m[2]!.toLowerCase() === "m" ? val / 1000 : val;
    }
  }
  return null;
}

export function getGroupKey(model: ModelResult, dim: GroupByDimension): string {
  switch (dim) {
    case "type":
      return model.type;
    case "tier":
      for (const t of model.tags) {
        if (["tier1", "tier2", "baseline", "extraction-specialist"].includes(t)) return t;
      }
      return "other";
    case "size": {
      const b = parseSizeB(model.tags);
      if (b === null) return "unknown";
      if (b < 1) return "small (<1B)";
      if (b <= 8) return "medium (1-8B)";
      return "large (>8B)";
    }
    case "runtime":
      return model.tags.includes("cloud") ? "cloud" : "local";
    case "none":
      return "all";
  }
}

export function useModelGrouping<T extends { name: string }>(
  models: ModelResult[],
  rows: T[],
  groupBy: GroupByDimension,
): [string, T[]][] {
  return useMemo(() => {
    // Build group key lookup from ModelResult tags
    const keyOf: Record<string, string> = {};
    for (const m of models) keyOf[m.name] = getGroupKey(m, groupBy);

    // Group rows
    const map: Record<string, T[]> = {};
    for (const r of rows) {
      const key = keyOf[r.name] ?? "other";
      (map[key] ??= []).push(r);
    }

    // Sort groups
    const order = GROUP_SORT_ORDER[groupBy];
    return Object.entries(map).sort((a, b) => (order[a[0]] ?? 99) - (order[b[0]] ?? 99));
  }, [models, rows, groupBy]);
}
