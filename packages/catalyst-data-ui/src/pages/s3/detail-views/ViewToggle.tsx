import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ViewMode } from "../hooks/useExplorerState";

export interface ViewOption {
  mode: ViewMode;
  label: string;
  icon: LucideIcon;
}

interface ViewToggleProps {
  options: ViewOption[];
  active: ViewMode | null;
  onChange: (v: ViewMode | null) => void;
}

/** Button group for switching between viewer modes. Clicking the active
 *  mode again toggles back to the kind's default by passing `null`. */
export function ViewToggle({ options, active, onChange }: ViewToggleProps) {
  return (
    <div
      data-testid="s3-view-toggle"
      className="flex items-center bg-surface-2 rounded border border-white/5 overflow-hidden"
    >
      {options.map(({ mode, label, icon: Icon }) => {
        const isActive = active === mode;
        return (
          <button
            key={mode}
            data-testid={`s3-view-${mode}`}
            data-active={isActive ? "true" : "false"}
            title={`View as ${label}`}
            onClick={() => onChange(isActive ? null : mode)}
            className={cn(
              "flex items-center gap-1 px-2 h-6 text-[10px] font-mono transition-colors",
              isActive
                ? "bg-cyan-500/15 text-cyan-300"
                : "text-zinc-500 hover:text-zinc-300 hover:bg-white/5",
            )}
          >
            <Icon className="h-3 w-3" />
            {label}
          </button>
        );
      })}
    </div>
  );
}
