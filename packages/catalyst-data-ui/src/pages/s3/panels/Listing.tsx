import { useRef, useMemo } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Folder, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { S3File, S3Folder } from "@/api/client";
import { fileIcon, formatBytes, formatDate, layerColor } from "../utils";

const ROW_HEIGHT = 44;

type Row = { kind: "folder"; folder: S3Folder } | { kind: "file"; file: S3File };

interface ListingProps {
  folders: S3Folder[];
  files: S3File[];
  selectedKey: string | null;
  highlightedIndex: number | null;
  onNavigate: (prefix: string) => void;
  onSelectFile: (file: S3File) => void;
  onHover?: (index: number) => void;
  /** When provided, each row gets a small red trash icon on the right.
   *  The handler receives the full row payload so the page-level dialog
   *  can prompt + dispatch the right backend call (object vs prefix). */
  onDelete?: (row: Row) => void;
}

/** Virtualized folder + file listing — folders first, then files.
 *
 *  All in one virtualizer so cursor selection (↑↓) flows naturally
 *  across the boundary. The `highlightedIndex` is the keyboard cursor;
 *  `selectedKey` is the file currently shown in the preview pane.
 */
export function Listing({
  folders,
  files,
  selectedKey,
  highlightedIndex,
  onNavigate,
  onSelectFile,
  onHover,
  onDelete,
}: ListingProps) {
  const parentRef = useRef<HTMLDivElement>(null);

  const rows = useMemo<Row[]>(
    () => [
      ...folders.map<Row>((folder) => ({ kind: "folder", folder })),
      ...files.map<Row>((file) => ({ kind: "file", file })),
    ],
    [folders, files],
  );

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  });

  if (rows.length === 0) {
    return <div className="p-4 text-sm text-zinc-600">Empty prefix</div>;
  }

  return (
    <div ref={parentRef} className="h-full overflow-auto">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
        {virtualizer.getVirtualItems().map((vi) => {
          const row = rows[vi.index];
          if (!row) return null;
          const isHighlighted = vi.index === highlightedIndex;
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
              {row.kind === "folder" ? (
                <FolderRow
                  folder={row.folder}
                  highlighted={isHighlighted}
                  onClick={onNavigate}
                  onDelete={onDelete ? () => onDelete(row) : undefined}
                />
              ) : (
                <FileRow
                  file={row.file}
                  selected={selectedKey === row.file.key}
                  highlighted={isHighlighted}
                  onClick={onSelectFile}
                  onDelete={onDelete ? () => onDelete(row) : undefined}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DeleteButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      data-testid="s3-delete-row"
      title={label}
      aria-label={label}
      onClick={(e) => {
        // Don't trigger the row's primary click (navigate / preview).
        e.stopPropagation();
        onClick();
      }}
      className="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity flex-shrink-0 h-6 w-6 rounded flex items-center justify-center text-zinc-500 hover:text-red-400 hover:bg-red-500/10"
    >
      <Trash2 className="h-3.5 w-3.5" />
    </button>
  );
}

function FolderRow({
  folder,
  highlighted,
  onClick,
  onDelete,
}: {
  folder: S3Folder;
  highlighted: boolean;
  onClick: (prefix: string) => void;
  onDelete?: () => void;
}) {
  const hasStats = folder.file_count !== undefined;
  return (
    <div
      className={cn(
        "group w-full h-full flex items-center gap-3 px-4 transition-colors border-b border-white/[0.03]",
        highlighted ? "bg-white/[0.06]" : "hover:bg-white/[0.04]",
      )}
    >
      <button
        data-testid="s3-folder-row"
        onClick={() => onClick(folder.prefix)}
        className="flex items-center gap-3 flex-1 min-w-0 text-left"
      >
        <Folder
          className={cn("h-4 w-4 flex-shrink-0", layerColor(folder.name) ?? "text-blue-400")}
        />
        <div className="flex-1 min-w-0">
          <span
            className={cn("text-sm truncate block", layerColor(folder.name) ?? "text-zinc-200")}
          >
            {folder.name}/
          </span>
          {hasStats && (
            <span className="text-[10px] text-zinc-600 font-mono" data-testid="s3-folder-stats">
              {folder.file_count} {folder.file_count === 1 ? "item" : "items"}
              {typeof folder.total_size === "number" && (
                <> &middot; {formatBytes(folder.total_size)}</>
              )}
              {folder.last_modified && <> &middot; {formatDate(folder.last_modified)}</>}
            </span>
          )}
        </div>
      </button>
      {onDelete && <DeleteButton label={`Delete ${folder.prefix}`} onClick={onDelete} />}
    </div>
  );
}

function FileRow({
  file,
  selected,
  highlighted,
  onClick,
  onDelete,
}: {
  file: S3File;
  selected: boolean;
  highlighted: boolean;
  onClick: (file: S3File) => void;
  onDelete?: () => void;
}) {
  const Icon = fileIcon(file.name);
  return (
    <div
      className={cn(
        "group w-full h-full flex items-center gap-3 px-4 transition-colors border-b border-white/[0.03]",
        selected ? "bg-white/[0.10]" : highlighted ? "bg-white/[0.06]" : "hover:bg-white/[0.04]",
      )}
    >
      <button
        data-testid="s3-file-row"
        onClick={() => onClick(file)}
        className="flex items-center gap-3 flex-1 min-w-0 text-left"
      >
        <Icon className="h-4 w-4 flex-shrink-0 text-zinc-500" />
        <div className="flex-1 min-w-0">
          <span className="text-sm text-zinc-300 truncate block">{file.name}</span>
          <span className="text-[10px] text-zinc-600 font-mono">
            {formatBytes(file.size)} &middot; {formatDate(file.last_modified)}
          </span>
        </div>
      </button>
      {onDelete && <DeleteButton label={`Delete ${file.key}`} onClick={onDelete} />}
    </div>
  );
}
