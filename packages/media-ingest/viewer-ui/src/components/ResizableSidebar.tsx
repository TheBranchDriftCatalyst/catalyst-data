import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { GripVertical } from "lucide-react";

interface ResizableSidebarProps {
  children: ReactNode;
  /** Persistence key so width survives reload. Without it, every page
   *  load resets to `defaultWidth`. */
  storageKey?: string;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  /** ``"left"`` puts the drag handle on the right edge (collapsing toward
   *  the right shrinks the sidebar). The opposite layout isn't supported
   *  yet — file an issue if you need it. */
  side?: "left";
  className?: string;
}

/** Horizontal resizable sidebar. Mirrors the vertical ``ResizablePanel``
 *  shape (drag handle + ``localStorage`` persistence) but on the X-axis.
 *  Drag the right edge to resize; release to commit. */
export default function ResizableSidebar({
  children,
  storageKey,
  defaultWidth = 240,
  minWidth = 160,
  maxWidth = 600,
  side: _side = "left",
  className = "",
}: ResizableSidebarProps) {
  const [width, setWidth] = useState<number>(() => {
    if (storageKey && typeof window !== "undefined") {
      const stored = window.localStorage.getItem(storageKey);
      const n = stored ? parseInt(stored, 10) : NaN;
      if (Number.isFinite(n) && n >= minWidth && n <= maxWidth) return n;
    }
    return defaultWidth;
  });

  const isDragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDragging.current = true;
      startX.current = e.clientX;
      startWidth.current = width;
      document.body.style.cursor = "ew-resize";
      document.body.style.userSelect = "none";
    },
    [width],
  );

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = e.clientX - startX.current;
      const next = Math.min(maxWidth, Math.max(minWidth, startWidth.current + delta));
      setWidth(next);
    };
    const handleMouseUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      if (storageKey && typeof window !== "undefined") {
        try {
          window.localStorage.setItem(storageKey, String(width));
        } catch {
          /* quota / private mode — ignore */
        }
      }
    };
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [maxWidth, minWidth, storageKey, width]);

  return (
    <div
      data-testid="resizable-sidebar"
      className={`relative flex-shrink-0 ${className}`}
      style={{ width }}
    >
      {children}
      {/* Drag handle — sliver on the right edge, widens on hover. */}
      <div
        data-testid="resize-handle-x"
        onMouseDown={handleMouseDown}
        className="group absolute top-0 right-0 h-full w-1 cursor-ew-resize hover:bg-cyan-500/30 active:bg-cyan-500/50 transition-colors z-10"
      >
        <div className="absolute top-1/2 -translate-y-1/2 -right-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <GripVertical className="h-4 w-4 text-zinc-500" />
        </div>
      </div>
    </div>
  );
}
