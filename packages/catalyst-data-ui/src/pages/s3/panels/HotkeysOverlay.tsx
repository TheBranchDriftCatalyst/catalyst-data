import { X } from "lucide-react";

interface HotkeysOverlayProps {
  open: boolean;
  onClose: () => void;
}

const HOTKEYS: { key: string; description: string }[] = [
  { key: "/", description: "Focus search" },
  { key: "⌘K / Ctrl+K", description: "Focus search" },
  { key: "↑ / ↓", description: "Move cursor through listing or search hits" },
  { key: "Enter", description: "Open folder or preview file (under cursor)" },
  { key: "Esc", description: "Clear search / close preview" },
  { key: "u", description: "Go up one prefix level" },
  { key: "g", description: "Jump to bucket root" },
  { key: "?", description: "Toggle this overlay" },
];

export function HotkeysOverlay({ open, onClose }: HotkeysOverlayProps) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        data-testid="s3-hotkeys-overlay"
        onClick={(e) => e.stopPropagation()}
        className="bg-surface-1 border border-white/10 rounded-lg shadow-xl p-5 w-[420px] max-w-[90vw]"
      >
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-zinc-200">Keyboard shortcuts</h2>
          <button
            onClick={onClose}
            className="text-zinc-500 hover:text-zinc-300 transition-colors"
            aria-label="Close shortcuts"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-1.5">
          {HOTKEYS.map(({ key, description }) => (
            <div key={key} className="flex items-center justify-between text-xs">
              <span className="text-zinc-400">{description}</span>
              <kbd className="px-2 py-0.5 rounded bg-surface-2 border border-white/10 text-[10px] font-mono text-cyan-300">
                {key}
              </kbd>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
