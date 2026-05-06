import { Folder, FlaskConical } from "lucide-react";
import { cn } from "@/lib/utils";
import { layerColor } from "../utils";

interface Pin {
  prefix: string;
  label: string;
  hint: string;
}

const PINS: Pin[] = [
  { prefix: "bronze/", label: "bronze", hint: "raw ingest" },
  { prefix: "silver/", label: "silver", hint: "normalized" },
  { prefix: "gold/", label: "gold", hint: "refined / partitioned" },
  { prefix: "platinum/", label: "platinum", hint: "production aggregates" },
  { prefix: "bench/", label: "bench", hint: "benchmark artifacts" },
];

interface PinnedRailProps {
  currentPrefix: string;
  recent: string[];
  onNavigate: (prefix: string) => void;
}

/** Left rail with one-click jumps to medallion layers + last-visited prefixes.
 *
 *  Stays narrow (collapsed-style) to leave the listing/preview pair maximum
 *  width. Active layer is highlighted by the breadcrumb prefix the user is
 *  currently inside, not just exact equality.
 */
export function PinnedRail({ currentPrefix, recent, onNavigate }: PinnedRailProps) {
  return (
    <aside className="w-44 flex-shrink-0 border-r border-white/5 bg-surface-1 flex flex-col">
      <div className="px-3 py-2 text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
        Layers
      </div>
      <div className="flex flex-col">
        {PINS.map((pin) => {
          const active = currentPrefix.startsWith(pin.prefix);
          const Icon = pin.prefix.startsWith("bench") ? FlaskConical : Folder;
          return (
            <button
              key={pin.prefix}
              data-testid={`s3-pin-${pin.label}`}
              onClick={() => onNavigate(pin.prefix)}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 text-left text-xs font-mono transition-colors",
                active ? "bg-white/[0.06]" : "hover:bg-white/[0.04]",
              )}
            >
              <Icon className={cn("h-3.5 w-3.5", layerColor(pin.label))} />
              <div className="min-w-0 flex-1">
                <div className={cn("truncate", layerColor(pin.label) ?? "text-zinc-300")}>
                  {pin.label}
                </div>
                <div className="text-[9px] text-zinc-600 truncate">{pin.hint}</div>
              </div>
            </button>
          );
        })}
      </div>

      {recent.length > 0 && (
        <>
          <div className="px-3 pt-3 pb-1 text-[10px] uppercase tracking-wider text-zinc-500 font-mono border-t border-white/5 mt-2">
            Recent
          </div>
          <div className="flex flex-col overflow-y-auto">
            {recent.map((prefix) => (
              <button
                key={prefix}
                onClick={() => onNavigate(prefix)}
                title={prefix}
                className={cn(
                  "px-3 py-1 text-left text-[11px] font-mono truncate transition-colors",
                  prefix === currentPrefix
                    ? "bg-white/[0.06] text-cyan-300"
                    : "text-zinc-500 hover:text-zinc-300 hover:bg-white/[0.04]",
                )}
              >
                {trimRecent(prefix)}
              </button>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

/** Show only the last 2 path segments so the rail doesn't blow out. */
function trimRecent(prefix: string): string {
  const parts = prefix.replace(/\/$/, "").split("/");
  if (parts.length <= 2) return prefix;
  return ".../" + parts.slice(-2).join("/") + "/";
}
