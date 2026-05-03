import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Badge, Button, ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { Copy, Check, Download, ExternalLink, LayoutGrid, FileText } from "lucide-react";
import { fetchS3Read, s3RawUrl } from "@/api/client";
import type { S3File } from "@/api/client";
import { JsonlTable } from "./JsonlTable";
import { MediaPreview } from "./MediaPreview";
import { fileKind, formatBytes, parseMediaDeepLink } from "./utils";

interface PreviewProps {
  file: S3File;
  onClose: () => void;
}

type CopyTarget = "key" | "s3url";

/** Right-pane file preview. Routes by `fileKind(name)` to the right renderer
 *  and surfaces deep-links / download / copy affordances in a sticky header.
 */
export function Preview({ file, onClose }: PreviewProps) {
  const [copied, setCopied] = useState<CopyTarget | null>(null);
  const [viewMode, setViewMode] = useState<"table" | "raw">("table");

  const kind = fileKind(file.name);
  const deepLink = parseMediaDeepLink(file.key);

  // Skip the read query for media kinds — they stream via /raw directly.
  const fetchable = kind !== "image" && kind !== "audio" && kind !== "video";
  const { data: content, isLoading } = useQuery({
    queryKey: ["s3-read", file.key],
    queryFn: () => fetchS3Read(file.key),
    enabled: fetchable,
    staleTime: 60_000,
  });

  const copy = useCallback((value: string, target: CopyTarget) => {
    navigator.clipboard.writeText(value);
    setCopied(target);
    window.setTimeout(() => setCopied(null), 1500);
  }, []);

  return (
    <div data-testid="s3-preview" className="flex-1 flex flex-col min-w-0 border-l border-white/5">
      {/* Header */}
      <div className="flex-shrink-0 px-4 py-2 border-b border-white/5 bg-surface-1">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-medium text-zinc-200 truncate">{file.name}</p>
            <p className="text-[10px] text-zinc-600 font-mono truncate">{file.key}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Badge variant="secondary" className="text-[10px]">
              {formatBytes(file.size)}
            </Badge>

            {kind === "jsonl" && content && Array.isArray(content.data) && (
              <Button
                variant="ghost"
                size="icon-sm"
                title={viewMode === "table" ? "Switch to raw JSON" : "Switch to table view"}
                onClick={() => setViewMode(viewMode === "table" ? "raw" : "table")}
              >
                {viewMode === "table" ? (
                  <FileText className="h-3.5 w-3.5" />
                ) : (
                  <LayoutGrid className="h-3.5 w-3.5" />
                )}
              </Button>
            )}

            {deepLink && (
              <Button asChild variant="ghost" size="sm" className="h-7 text-xs">
                <Link to={`/player/${encodeURIComponent(deepLink.documentId)}`}>
                  <ExternalLink className="h-3 w-3 mr-1" />
                  Open in player
                </Link>
              </Button>
            )}

            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Copy s3:// URL"
              title="Copy s3:// URL"
              onClick={() => copy(`s3://dagster/${file.key}`, "s3url")}
            >
              {copied === "s3url" ? (
                <Check className="h-3.5 w-3.5 text-green-400" />
              ) : (
                <span className="text-[10px] font-mono leading-none">s3://</span>
              )}
            </Button>

            <Button
              variant="ghost"
              size="icon-sm"
              title="Copy key"
              onClick={() => copy(file.key, "key")}
            >
              {copied === "key" ? (
                <Check className="h-3.5 w-3.5 text-green-400" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </Button>

            <Button asChild variant="ghost" size="icon-sm" title="Download">
              <a href={s3RawUrl(file.key, true)} download>
                <Download className="h-3.5 w-3.5" />
              </a>
            </Button>

            <Button variant="ghost" size="sm" className="text-xs h-7" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {(kind === "image" || kind === "audio" || kind === "video") && (
          <MediaPreview kind={kind} s3Key={file.key} />
        )}

        {fetchable && isLoading && (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
          </div>
        )}

        {fetchable && content && <PreviewBody content={content} kind={kind} viewMode={viewMode} />}
      </div>
    </div>
  );
}

function PreviewBody({
  content,
  kind,
  viewMode,
}: {
  content: Awaited<ReturnType<typeof fetchS3Read>>;
  kind: ReturnType<typeof fileKind>;
  viewMode: "table" | "raw";
}) {
  if (content.error) {
    return (
      <div className="p-4">
        <div className="text-sm text-red-400 mb-2">{content.error}</div>
        {content.preview && (
          <div className="text-sm text-zinc-500 font-mono">{content.preview}</div>
        )}
      </div>
    );
  }

  if (kind === "jsonl" && Array.isArray(content.data) && viewMode === "table") {
    return (
      <div className="flex flex-col h-full">
        {content.truncated && (
          <div className="px-4 py-2 text-xs text-amber-400 border-b border-white/5">
            Showing {content.data.length} of {content.total_lines ?? "many"} rows (truncated)
          </div>
        )}
        <div className="flex-1 min-h-0">
          <JsonlTable rows={content.data as Record<string, unknown>[]} />
        </div>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        {content.truncated && (
          <div className="text-xs text-amber-400 mb-2">
            Showing {Array.isArray(content.data) ? (content.data as unknown[]).length : "partial"}{" "}
            of {content.total_lines ?? "many"} lines (truncated)
          </div>
        )}
        {content.preview && !content.data && (
          <div className="text-sm text-zinc-500 font-mono">{content.preview}</div>
        )}
        {content.data != null && (
          <pre className="text-xs text-zinc-300 font-mono whitespace-pre-wrap break-all leading-relaxed">
            {typeof content.data === "string"
              ? content.data
              : JSON.stringify(content.data, null, 2)}
          </pre>
        )}
      </div>
    </ScrollArea>
  );
}
