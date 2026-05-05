/**
 * Active ground truth badge — displays the current GT set name + mention count.
 *
 * - Reads active run ID from URL param `?run=`
 * - Resolves GT name + mention_count from `useRunReport`
 * - Clickable if `/viewer/api/bench/ground-truth` endpoint exists (list available GT sets)
 * - URL param: `?gt=<name>`, localStorage key: `viewer:activeGt`
 * - Gracefully handles no active run, no GT, and loading states
 */

import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ChevronDown } from "lucide-react";
import { useRunReport } from "@/hooks/useRunReport";

interface GtListResponse {
  names: string[];
  uri: string;
}

/**
 * Get the active GT name from URL param, localStorage, or return undefined.
 */
function getActiveGtName(): string | undefined {
  const params = new URLSearchParams(window.location.search);
  const urlGt = params.get("gt");
  if (urlGt) return urlGt;
  return window.localStorage.getItem("viewer:activeGt") ?? undefined;
}

/**
 * Set the active GT name in URL and localStorage.
 */
function setActiveGtName(name: string): void {
  const params = new URLSearchParams(window.location.search);
  params.set("gt", name);
  window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  window.localStorage.setItem("viewer:activeGt", name);
}

export function ActiveGtBadge() {
  const [searchParams] = useSearchParams();
  const runId = searchParams.get("run");

  // Fetch the report for this run to get GT name + mention_count
  const { data: report } = useRunReport(runId);

  // Try to list available GT sets
  const { data: gtList } = useQuery<GtListResponse | null>({
    queryKey: ["bench", "ground-truth", "list"],
    queryFn: async () => {
      try {
        const res = await fetch("/viewer/api/bench/ground-truth");
        if (!res.ok) return null;
        return (await res.json()) as GtListResponse;
      } catch {
        return null;
      }
    },
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
  });

  // Determine if dropdown is available (endpoint exists and has multiple GTs)
  const hasGtList = gtList && gtList.names && gtList.names.length > 0;
  const activeGtName = useMemo(() => {
    const explicit = getActiveGtName();
    if (explicit) return explicit;
    // Fall back to the GT from the current run's report (if available)
    return report?.ground_truth?.reference_model;
  }, [report?.ground_truth?.reference_model]);

  const mentionCount = report?.ground_truth?.mention_count;

  // Badge content
  const label = activeGtName ? `GT: ${activeGtName} · ${mentionCount ?? "?"} mentions` : "No GT";
  const isBadgeDisabled = !activeGtName;

  // If no GT list available or GT is disabled, render read-only badge
  if (!hasGtList || isBadgeDisabled) {
    return (
      <div
        className={`flex items-center gap-1.5 px-3 h-8 rounded text-xs font-mono ${
          isBadgeDisabled
            ? "text-zinc-500"
            : "text-cyan-300 bg-cyan-950/30 border border-cyan-900/50"
        }`}
        data-testid="active-gt-badge"
      >
        {label}
      </div>
    );
  }

  // Dropdown available — render clickable badge
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          className="flex items-center gap-1.5 px-3 h-8 rounded text-xs font-mono text-cyan-300 bg-cyan-950/30 border border-cyan-900/50 hover:bg-cyan-950/50 hover:border-cyan-800/70 data-[state=open]:bg-cyan-950/60 data-[state=open]:border-cyan-700 transition-colors"
          data-testid="active-gt-badge"
        >
          {label}
          <ChevronDown className="h-3 w-3 opacity-60" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-[200px] rounded-md border border-white/10 bg-surface-1 shadow-xl py-1 outline-none"
        >
          <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
            Ground truth sets
          </div>
          {gtList?.names?.map((name: string) => (
            <DropdownMenu.Item
              key={name}
              onSelect={() => setActiveGtName(name)}
              className="px-3 py-2 text-xs font-mono text-zinc-300 hover:bg-white/[0.04] hover:text-cyan-300 cursor-pointer outline-none data-[highlighted]:bg-white/[0.04] data-[highlighted]:text-cyan-300"
            >
              <div className="flex items-center justify-between gap-3">
                <span>{name}</span>
                {activeGtName === name && <span className="text-cyan-300">✓</span>}
              </div>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
