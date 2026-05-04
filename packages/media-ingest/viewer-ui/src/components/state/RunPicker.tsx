import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ChevronDown, CircleDot, History } from "lucide-react";
import { cn } from "@/lib/utils";

interface RunPickerProps {
  /** Run IDs to show, newest-first. Each consumer filters/transforms its
   *  view of `/viewer/api/bench/runs` independently (e.g. the benchmark
   *  report only lists runs with a `report.json`; the state inspector
   *  shows everything). */
  runs: string[];
  /** Run ID of the currently in-flight bench, if any. Pulses next to the
   *  matching menu item. ``null`` means no live run. */
  liveRunId: string | null;
  /** The run currently being rendered. May differ from the latest if
   *  the user pinned an older run. ``null`` means "follow latest". */
  selectedRunId: string | null;
  /** Called with ``null`` for the "follow latest" entry, or a specific
   *  run ID otherwise. Parents persist the choice in URL state. */
  onSelect: (runId: string | null) => void;
  /** Trigger / menu copy for the auto-follow entry. Defaults to
   *  "Latest"; the benchmark report uses "Latest report" since its
   *  Latest endpoint hits the top-level report.json, not the newest
   *  archived run. */
  latestLabel?: string;
}

/** Shared run-picker dropdown — Radix DropdownMenu with one "auto-follow
 *  Latest" entry plus one entry per supplied run. The in-flight run
 *  pulses with a green CircleDot. Parents persist selection via URL
 *  state (typically `?run=<id>`) so reload + share work. */
export function RunPicker({
  runs,
  liveRunId,
  selectedRunId,
  onSelect,
  latestLabel = "Latest",
}: RunPickerProps) {
  const isFollowingLatest = selectedRunId === null;
  const label =
    selectedRunId == null
      ? latestLabel
      : selectedRunId === liveRunId
        ? `${selectedRunId} (live)`
        : selectedRunId;

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          data-testid="run-picker-trigger"
          type="button"
          className="flex items-center gap-1.5 px-2 h-6 rounded text-[10px] font-mono text-zinc-300 bg-white/[0.04] hover:bg-white/[0.08] data-[state=open]:bg-white/[0.10] transition-colors"
        >
          <History className="h-3 w-3 opacity-60" />
          <span className="truncate max-w-[200px]">{label}</span>
          <ChevronDown className="h-3 w-3 opacity-60" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={4}
          data-testid="run-picker-menu"
          className="z-50 min-w-[260px] max-h-[60vh] overflow-y-auto rounded-md border border-white/10 bg-surface-1 shadow-xl py-1 outline-none"
        >
          <DropdownMenu.Item
            data-testid="run-picker-latest"
            onSelect={() => onSelect(null)}
            className={cn(
              "flex items-center gap-2 px-3 py-1.5 text-xs font-mono cursor-pointer outline-none",
              isFollowingLatest
                ? "bg-cyan-500/10 text-cyan-300"
                : "text-zinc-300 hover:bg-white/[0.04] data-[highlighted]:bg-white/[0.04]",
            )}
          >
            <span className="text-[10px] uppercase tracking-wider opacity-60">{latestLabel}</span>
            <span className="text-zinc-500 truncate">{runs[0] ?? "no runs yet"}</span>
          </DropdownMenu.Item>

          {runs.length > 0 && (
            <>
              <DropdownMenu.Separator className="h-px bg-white/5 my-1" />
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-zinc-500">
                All runs ({runs.length})
              </div>
              {runs.map((id) => {
                const isLive = id === liveRunId;
                const isSelected = id === selectedRunId;
                return (
                  <DropdownMenu.Item
                    key={id}
                    data-testid={`run-picker-${id}`}
                    onSelect={() => onSelect(id)}
                    className={cn(
                      "flex items-center gap-2 px-3 py-1.5 text-xs font-mono cursor-pointer outline-none",
                      isSelected
                        ? "bg-cyan-500/10 text-cyan-300"
                        : "text-zinc-300 hover:bg-white/[0.04] data-[highlighted]:bg-white/[0.04]",
                    )}
                  >
                    <span className="truncate flex-1">{id}</span>
                    {isLive && (
                      <span className="flex items-center gap-1 text-[10px] text-emerald-400">
                        <CircleDot className="h-2.5 w-2.5 animate-pulse" />
                        live
                      </span>
                    )}
                  </DropdownMenu.Item>
                );
              })}
            </>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
