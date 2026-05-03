import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Badge, Button, ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { CodeBlock } from "@thebranchdriftcatalyst/catalyst-ui/components/CodeBlock";
import { MarkdownRenderer } from "@thebranchdriftcatalyst/catalyst-ui/components/MarkdownRenderer";
import {
  Copy,
  Check,
  Download,
  ExternalLink,
  FileText,
  LayoutGrid,
  ListTree,
  Code2,
  BookOpen,
} from "lucide-react";
import { fetchS3Read, s3RawUrl } from "@/api/client";
import type { S3File } from "@/api/client";
import { cn } from "@/lib/utils";
import { JsonlTable } from "./JsonlTable";
import { JsonTree } from "./JsonTree";
import { MediaPreview } from "./MediaPreview";
import { fileKind, formatBytes, parseMediaDeepLink, extOf, type FileKind } from "./utils";
import type { ViewMode } from "./useExplorerState";

interface PreviewProps {
  file: S3File;
  view: ViewMode | null;
  onViewChange: (v: ViewMode | null) => void;
  onClose: () => void;
}

type CopyTarget = "key" | "s3url";

/** Per-file-kind view-mode menu. The first entry is the default when the
 *  user hasn't picked a `?view=` for the current file. Each entry is a
 *  `(label, icon, view)` triple — the toggle button group reads from this. */
const VIEW_OPTIONS: Record<FileKind, { mode: ViewMode; label: string; icon: typeof FileText }[]> = {
  jsonl: [
    { mode: "table", label: "Table", icon: LayoutGrid },
    { mode: "tree", label: "Tree", icon: ListTree },
    { mode: "raw", label: "Raw", icon: FileText },
  ],
  json: [
    { mode: "tree", label: "Tree", icon: ListTree },
    { mode: "raw", label: "Raw", icon: FileText },
  ],
  text: [
    { mode: "markdown", label: "Rendered", icon: BookOpen },
    { mode: "raw", label: "Raw", icon: FileText },
  ],
  code: [
    { mode: "code", label: "Code", icon: Code2 },
    { mode: "raw", label: "Raw", icon: FileText },
  ],
  // Media kinds + binary don't get a toggle — they have one canonical view.
  image: [],
  audio: [],
  video: [],
  binary: [],
};

/** Resolve which view the body should render: explicit `view` param if it
 *  applies to this kind, else the kind's default (first option), else null. */
function resolveView(kind: FileKind, requested: ViewMode | null): ViewMode | null {
  const opts = VIEW_OPTIONS[kind];
  if (opts.length === 0) return null;
  if (requested && opts.some((o) => o.mode === requested)) return requested;
  return opts[0]!.mode;
}

/** Right-pane file preview. Routes by `fileKind(name)` and the active view
 *  to the right renderer; surfaces deep-links / download / copy affordances
 *  in a sticky header. View-mode toggles persist via URL `?view=...`.
 */
export function Preview({ file, view, onViewChange, onClose }: PreviewProps) {
  const [copied, setCopied] = useState<CopyTarget | null>(null);

  const kind = fileKind(file.name);
  const deepLink = parseMediaDeepLink(file.key);
  const activeView = resolveView(kind, view);
  const viewOptions = VIEW_OPTIONS[kind];

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

            {viewOptions.length > 0 && (
              <ViewToggle options={viewOptions} active={activeView} onChange={onViewChange} />
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

        {fetchable && content && (
          <PreviewBody content={content} kind={kind} view={activeView} fileName={file.name} />
        )}
      </div>
    </div>
  );
}

function ViewToggle({
  options,
  active,
  onChange,
}: {
  options: { mode: ViewMode; label: string; icon: typeof FileText }[];
  active: ViewMode | null;
  onChange: (v: ViewMode | null) => void;
}) {
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

function PreviewBody({
  content,
  kind,
  view,
  fileName,
}: {
  content: Awaited<ReturnType<typeof fetchS3Read>>;
  kind: FileKind;
  view: ViewMode | null;
  fileName: string;
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

  // ── JSONL ──────────────────────────────────────────────────────────────
  if (kind === "jsonl" && Array.isArray(content.data)) {
    if (view === "table") {
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
    if (view === "tree") {
      return (
        <ScrollArea className="h-full">
          {content.truncated && <TruncationBanner content={content} />}
          <JsonTree data={content.data} collapseDepth={1} />
        </ScrollArea>
      );
    }
    // raw
    return <RawPre content={content} />;
  }

  // ── JSON ───────────────────────────────────────────────────────────────
  if (kind === "json" && content.data != null) {
    if (view === "tree") {
      return (
        <ScrollArea className="h-full">
          <JsonTree data={content.data} collapseDepth={2} />
        </ScrollArea>
      );
    }
    return <RawPre content={content} />;
  }

  // ── Markdown / text ────────────────────────────────────────────────────
  if (kind === "text" && typeof content.data === "string") {
    if (view === "markdown" && extOf(fileName) === "md") {
      return (
        <ScrollArea className="h-full">
          <div className="p-4 prose prose-invert prose-sm max-w-none">
            <MarkdownRenderer content={content.data} />
          </div>
        </ScrollArea>
      );
    }
    return <RawPre content={content} />;
  }

  // ── Code ───────────────────────────────────────────────────────────────
  if (kind === "code" && typeof content.data === "string") {
    if (view === "code") {
      return (
        <ScrollArea className="h-full">
          <div className="p-2">
            <CodeBlock
              code={content.data}
              language={extOf(fileName) || "txt"}
              showLineNumbers
              showCopyButton
              useCardContext={false}
            />
          </div>
        </ScrollArea>
      );
    }
    return <RawPre content={content} />;
  }

  // ── Fallback (binary / unknown) ────────────────────────────────────────
  return <RawPre content={content} />;
}

function TruncationBanner({ content }: { content: Awaited<ReturnType<typeof fetchS3Read>> }) {
  const shown = Array.isArray(content.data) ? content.data.length : "partial";
  return (
    <div className="px-4 py-2 text-xs text-amber-400 border-b border-white/5">
      Showing {shown} of {content.total_lines ?? "many"} lines (truncated)
    </div>
  );
}

function RawPre({ content }: { content: Awaited<ReturnType<typeof fetchS3Read>> }) {
  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        {content.truncated && <TruncationBanner content={content} />}
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
