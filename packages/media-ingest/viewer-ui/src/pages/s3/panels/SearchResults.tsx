import { useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Folder } from "lucide-react";
import { cn } from "@/lib/utils";
import type { S3SearchHit } from "@/api/client";
import { fileIcon, formatBytes, formatDate, highlightRuns } from "../utils";

const ROW_HEIGHT = 52;

interface SearchResultsProps {
  hits: S3SearchHit[];
  total: number;
  truncated: boolean;
  highlightedIndex: number | null;
  selectedKey: string | null;
  onActivate: (hit: S3SearchHit) => void;
  onHover?: (index: number) => void;
}

/** Virtualized fuzzy-search hit list with matched-character highlighting.
 *
 *  Each hit shows the full key with the matched chars highlighted, plus
 *  size + last-modified. Folders (keys ending in `/`) are rendered with
 *  the folder icon and activate as navigation; files activate as preview.
 *  The parent decides what "activate" means via `onActivate`.
 */
export function SearchResults({
  hits,
  total,
  truncated,
  highlightedIndex,
  selectedKey,
  onActivate,
  onHover,
}: SearchResultsProps) {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: hits.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  });

  if (hits.length === 0) {
    return <div className="p-4 text-sm text-zinc-600">No matches</div>;
  }

  return (
    <div ref={parentRef} className="h-full overflow-auto">
      {truncated && (
        <div className="px-4 py-2 text-[11px] text-zinc-500 border-b border-white/5 bg-surface-1">
          Showing {hits.length} of {total} matches — refine your query for more
        </div>
      )}
      <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
        {virtualizer.getVirtualItems().map((vi) => {
          const hit = hits[vi.index];
          if (!hit) return null;
          const isFolder = hit.key.endsWith("/");
          const Icon = isFolder ? Folder : fileIcon(hit.name);
          const highlighted = vi.index === highlightedIndex;
          const selected = selectedKey === hit.key;
          return (
            <div
              key={vi.key}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${vi.start}px)`,
                height: ROW_HEIGHT,
              }}
              onMouseEnter={() => onHover?.(vi.index)}
            >
              <button
                onClick={() => onActivate(hit)}
                className={cn(
                  "w-full h-full flex items-center gap-3 px-4 text-left border-b border-white/[0.03] transition-colors",
                  selected
                    ? "bg-white/[0.10]"
                    : highlighted
                      ? "bg-white/[0.06]"
                      : "hover:bg-white/[0.04]",
                )}
              >
                <Icon
                  className={cn(
                    "h-4 w-4 flex-shrink-0",
                    isFolder ? "text-blue-400" : "text-zinc-500",
                  )}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-mono text-zinc-300 truncate">
                    <KeyHighlight haystack={hit.key} indices={hit.match_indices} />
                  </div>
                  <div className="text-[10px] text-zinc-600 font-mono mt-0.5">
                    {formatBytes(hit.size)} &middot; {formatDate(hit.last_modified)}
                  </div>
                </div>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function KeyHighlight({ haystack, indices }: { haystack: string; indices: number[] }) {
  const runs = highlightRuns(haystack, indices);
  return (
    <>
      {runs.map((run, i) =>
        run.matched ? (
          <span key={i} className="text-cyan-300 font-semibold">
            {run.text}
          </span>
        ) : (
          <span key={i}>{run.text}</span>
        ),
      )}
    </>
  );
}
