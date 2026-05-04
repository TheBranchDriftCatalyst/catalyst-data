import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ChevronDown, CircleDot, History } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RunsListing } from "@/hooks/useRuns";

interface RunPickerProps {
  /** Runs newest-first, plus which one is currently in-flight. */
  runs: RunsListing;
  /** The run currently being read (from `useRunStream.runId`). May
   *  differ from `runs.latest` if the user pinned an older run. */
  selectedRunId: string | null;
  /** ``null`` clears the URL pin so the hook follows `/runs?latest`. */
  onSelect: (runId: string | null) => void;
}

/** Run-picker dropdown for the StateInspector header. Defaults to
 *  "Latest" (follow `/runs.latest`) and lists every archived run with a
 *  green dot next to the live one. Pinning a specific run sets `?run=<id>`
 *  in the URL via the parent's `onSelect` so reload + share work. */
export function RunPicker({ runs, selectedRunId, onSelect }: RunPickerProps) {
  const isFollowingLatest = selectedRunId === null || selectedRunId === runs.latest;
  const label =
    selectedRunId == null
      ? "Latest"
      : selectedRunId === runs.live
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
            <span className="text-[10px] uppercase tracking-wider opacity-60">Latest</span>
            <span className="text-zinc-500 truncate">{runs.latest ?? "no runs yet"}</span>
          </DropdownMenu.Item>

          {runs.runs.length > 0 && (
            <>
              <DropdownMenu.Separator className="h-px bg-white/5 my-1" />
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-zinc-500">
                All runs ({runs.runs.length})
              </div>
              {runs.runs.map((id) => {
                const isLive = id === runs.live;
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
