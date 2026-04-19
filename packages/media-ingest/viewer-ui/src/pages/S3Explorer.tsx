import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { ScrollArea, Badge, Button, Input } from "@thebranchdriftcatalyst/catalyst-ui";
import {
  Folder,
  FileText,
  FileJson,
  File,
  ChevronRight,
  ArrowLeft,
  Database,
  Copy,
  Check,
} from "lucide-react";
import { fetchS3List, fetchS3Read } from "@/api/client";
import type { S3File } from "@/api/client";
import { cn } from "@/lib/utils";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 1 : 0)} ${sizes[i]}`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fileIcon(name: string) {
  if (name.endsWith(".json") || name.endsWith(".jsonl")) return FileJson;
  if (name.endsWith(".txt") || name.endsWith(".md") || name.endsWith(".prompt")) return FileText;
  return File;
}

/** Color for medallion layer prefixes */
function layerColor(prefix: string): string | undefined {
  const p = prefix.toLowerCase();
  if (p.startsWith("bronze")) return "text-amber-500";
  if (p.startsWith("silver")) return "text-zinc-400";
  if (p.startsWith("gold")) return "text-yellow-400";
  if (p.startsWith("platinum")) return "text-cyan-400";
  return undefined;
}

export default function S3Explorer() {
  const [prefix, setPrefix] = useState("");
  const [selectedFile, setSelectedFile] = useState<S3File | null>(null);
  const [pathInput, setPathInput] = useState("");
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const {
    data: listing,
    isLoading: listLoading,
    isError: listError,
  } = useQuery({
    queryKey: ["s3-list", prefix],
    queryFn: () => fetchS3List(prefix),
    staleTime: 30_000,
  });

  const { data: fileContent, isLoading: fileLoading } = useQuery({
    queryKey: ["s3-read", selectedFile?.key],
    queryFn: () => fetchS3Read(selectedFile!.key),
    enabled: !!selectedFile,
    staleTime: 60_000,
  });

  const navigate = useCallback((newPrefix: string) => {
    setPrefix(newPrefix);
    setSelectedFile(null);
  }, []);

  const goUp = useCallback(() => {
    // "gold/media_ingest/media/" → "gold/media_ingest/"
    const parts = prefix.replace(/\/$/, "").split("/");
    parts.pop();
    navigate(parts.length > 0 ? parts.join("/") + "/" : "");
  }, [prefix, navigate]);

  const jumpToPath = useCallback(() => {
    const p = pathInput.trim();
    if (p) {
      const normalized = p.endsWith("/") ? p : p + "/";
      navigate(normalized);
      setPathInput("");
    }
  }, [pathInput, navigate]);

  const copyKey = useCallback((key: string) => {
    navigator.clipboard.writeText(key);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  }, []);

  // Breadcrumbs from prefix
  const breadcrumbs = prefix
    .split("/")
    .filter(Boolean)
    .map((part, i, arr) => ({
      label: part,
      prefix: arr.slice(0, i + 1).join("/") + "/",
    }));

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-4 py-3 border-b border-white/5">
        <div className="flex items-center gap-3">
          <Database className="h-5 w-5 text-cyan-400" />
          <h1 className="text-lg font-semibold">S3 Explorer</h1>
          <Badge variant="secondary" className="text-[10px]">
            {listing ? `${listing.folders.length} folders, ${listing.files.length} files` : "..."}
          </Badge>
        </div>

        {/* Breadcrumbs */}
        <div className="flex items-center gap-1 mt-2 text-xs">
          <button
            onClick={() => navigate("")}
            className={cn(
              "hover:text-white transition-colors px-1 py-0.5 rounded",
              prefix === "" ? "text-cyan-400 font-medium" : "text-zinc-400",
            )}
          >
            bucket
          </button>
          {breadcrumbs.map((bc) => (
            <span key={bc.prefix} className="flex items-center gap-1">
              <ChevronRight className="h-3 w-3 text-zinc-600" />
              <button
                onClick={() => navigate(bc.prefix)}
                className={cn(
                  "hover:text-white transition-colors px-1 py-0.5 rounded",
                  bc.prefix === prefix
                    ? "text-cyan-400 font-medium"
                    : (layerColor(bc.label) ?? "text-zinc-400"),
                )}
              >
                {bc.label}
              </button>
            </span>
          ))}
        </div>

        {/* Path jump */}
        <div className="flex gap-2 mt-2">
          <Input
            placeholder="Jump to path (e.g. gold/media_ingest/media/)"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && jumpToPath()}
            className="flex-1 h-7 text-xs bg-surface-2 border-white/5"
          />
          {prefix && (
            <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={goUp}>
              <ArrowLeft className="h-3.5 w-3.5 mr-1" />
              Up
            </Button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 flex min-h-0">
        {/* File listing */}
        <div
          className={cn("border-r border-white/5 flex flex-col", selectedFile ? "w-1/3" : "flex-1")}
        >
          <ScrollArea className="flex-1">
            {listLoading && (
              <div className="flex items-center justify-center py-8">
                <div className="w-5 h-5 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
              </div>
            )}

            {listError && <div className="p-4 text-sm text-red-400">Failed to list objects</div>}

            {listing && (
              <div className="divide-y divide-white/[0.03]">
                {/* Folders */}
                {listing.folders.map((folder) => (
                  <button
                    key={folder.prefix}
                    onClick={() => navigate(folder.prefix)}
                    className="w-full flex items-center gap-3 px-4 py-2 hover:bg-white/[0.04] transition-colors text-left"
                  >
                    <Folder
                      className={cn(
                        "h-4 w-4 flex-shrink-0",
                        layerColor(folder.name) ?? "text-blue-400",
                      )}
                    />
                    <span
                      className={cn("text-sm flex-1", layerColor(folder.name) ?? "text-zinc-200")}
                    >
                      {folder.name}/
                    </span>
                  </button>
                ))}

                {/* Files */}
                {listing.files.map((file) => {
                  const Icon = fileIcon(file.name);
                  const isSelected = selectedFile?.key === file.key;
                  return (
                    <button
                      key={file.key}
                      onClick={() => setSelectedFile(file)}
                      className={cn(
                        "w-full flex items-center gap-3 px-4 py-2 transition-colors text-left",
                        isSelected ? "bg-white/[0.08]" : "hover:bg-white/[0.04]",
                      )}
                    >
                      <Icon className="h-4 w-4 flex-shrink-0 text-zinc-500" />
                      <div className="flex-1 min-w-0">
                        <span className="text-sm text-zinc-300 truncate block">{file.name}</span>
                        <span className="text-[10px] text-zinc-600 font-mono">
                          {formatBytes(file.size)} &middot; {formatDate(file.last_modified)}
                        </span>
                      </div>
                    </button>
                  );
                })}

                {listing.folders.length === 0 && listing.files.length === 0 && (
                  <div className="p-4 text-sm text-zinc-600">Empty prefix</div>
                )}
              </div>
            )}
          </ScrollArea>
        </div>

        {/* File preview panel */}
        {selectedFile && (
          <div className="flex-1 flex flex-col min-w-0">
            {/* File header */}
            <div className="flex-shrink-0 px-4 py-2 border-b border-white/5 bg-surface-1">
              <div className="flex items-center justify-between">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-zinc-200 truncate">{selectedFile.name}</p>
                  <p className="text-[10px] text-zinc-600 font-mono truncate">{selectedFile.key}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <Badge variant="secondary" className="text-[10px]">
                    {formatBytes(selectedFile.size)}
                  </Badge>
                  <Button variant="ghost" size="icon-sm" onClick={() => copyKey(selectedFile.key)}>
                    {copiedKey === selectedFile.key ? (
                      <Check className="h-3.5 w-3.5 text-green-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs h-7"
                    onClick={() => setSelectedFile(null)}
                  >
                    Close
                  </Button>
                </div>
              </div>
            </div>

            {/* File content */}
            <ScrollArea className="flex-1">
              {fileLoading && (
                <div className="flex items-center justify-center py-8">
                  <div className="w-5 h-5 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
                </div>
              )}

              {fileContent && (
                <div className="p-4">
                  {fileContent.error && (
                    <div className="text-sm text-red-400 mb-3">{fileContent.error}</div>
                  )}

                  {fileContent.truncated && (
                    <div className="text-xs text-amber-400 mb-2">
                      Showing{" "}
                      {Array.isArray(fileContent.data)
                        ? (fileContent.data as unknown[]).length
                        : "partial"}{" "}
                      of {fileContent.total_lines ?? "many"} lines (truncated)
                    </div>
                  )}

                  {fileContent.preview && !fileContent.data && (
                    <div className="text-sm text-zinc-500 font-mono">{fileContent.preview}</div>
                  )}

                  {fileContent.data != null && (
                    <pre className="text-xs text-zinc-300 font-mono whitespace-pre-wrap break-all leading-relaxed">
                      {typeof fileContent.data === "string"
                        ? fileContent.data
                        : JSON.stringify(fileContent.data, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </ScrollArea>
          </div>
        )}
      </div>
    </div>
  );
}
