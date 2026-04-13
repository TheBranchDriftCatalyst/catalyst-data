import { useState, useRef, useCallback, useEffect, type ReactNode } from "react";
import { ChevronDown, ChevronUp, GripHorizontal } from "lucide-react";

interface ResizablePanelProps {
  children: ReactNode;
  defaultHeight?: number;
  minHeight?: number;
  maxHeight?: number;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  className?: string;
  /** Height of the collapsed tab bar strip */
  collapsedHeight?: number;
}

export default function ResizablePanel({
  children,
  defaultHeight = 280,
  minHeight = 120,
  maxHeight = 600,
  collapsed: controlledCollapsed,
  onCollapsedChange,
  className = "",
  collapsedHeight = 40,
}: ResizablePanelProps) {
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const collapsed = controlledCollapsed ?? internalCollapsed;
  const setCollapsed = onCollapsedChange ?? setInternalCollapsed;

  const [height, setHeight] = useState(defaultHeight);
  const panelRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);
  const startY = useRef(0);
  const startHeight = useRef(0);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (collapsed) return;
      e.preventDefault();
      isDragging.current = true;
      startY.current = e.clientY;
      startHeight.current = height;
      document.body.style.cursor = "ns-resize";
      document.body.style.userSelect = "none";
    },
    [height, collapsed],
  );

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return;
      // Dragging upward increases height (mouse goes up = clientY decreases)
      const delta = startY.current - e.clientY;
      const newHeight = Math.min(maxHeight, Math.max(minHeight, startHeight.current + delta));
      setHeight(newHeight);
    };

    const handleMouseUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [maxHeight, minHeight]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed(!collapsed);
  }, [collapsed, setCollapsed]);

  return (
    <div
      ref={panelRef}
      data-testid="resizable-panel"
      className={`flex flex-col flex-shrink-0 bg-surface-1 border-t border-white/5 transition-[height] ${
        isDragging.current ? "" : "duration-200"
      } ${className}`}
      style={{ height: collapsed ? collapsedHeight : height }}
    >
      {/* Drag handle bar */}
      <div
        data-testid="resize-handle"
        className={`resizable-panel-handle group flex items-center justify-center flex-shrink-0 ${
          collapsed ? "cursor-pointer" : "cursor-ns-resize"
        }`}
        onMouseDown={collapsed ? undefined : handleMouseDown}
        onClick={collapsed ? toggleCollapsed : undefined}
      >
        {/* Grip indicator */}
        <div className="flex items-center gap-2">
          <GripHorizontal className="h-3 w-3 text-zinc-600 group-hover:text-zinc-400 transition-colors" />
        </div>

        {/* Collapse/expand button */}
        <button
          data-testid="collapse-toggle"
          onClick={(e) => {
            e.stopPropagation();
            toggleCollapsed();
          }}
          className="absolute right-3 p-0.5 rounded hover:bg-surface-3 text-zinc-600 hover:text-zinc-300 transition-colors"
        >
          {collapsed ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      {/* Panel content */}
      <div
        className={`flex-1 min-h-0 overflow-hidden transition-opacity ${
          collapsed ? "opacity-0 pointer-events-none" : "opacity-100"
        }`}
      >
        {children}
      </div>
    </div>
  );
}
