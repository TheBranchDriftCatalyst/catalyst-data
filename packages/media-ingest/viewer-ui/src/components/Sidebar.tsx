import { useState, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Input,
  ScrollArea,
  Separator,
  Badge,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@thebranchdriftcatalyst/catalyst-ui";
import {
  PanelLeftClose,
  PanelLeft,
  Search,
  Video,
  AudioLines,
  Tv2,
  ArrowRightLeft,
} from "lucide-react";
import { fetchDocuments } from "@/api/client";
import type { MediaDocument } from "@/types/media";
import { formatTime } from "@/lib/speakers";
import { cn } from "@/lib/utils";

type SortKey = "title" | "duration" | "source";

interface SidebarProps {
  className?: string;
  collapsed?: boolean;
  onToggle?: () => void;
}

export default function Sidebar({ className = "", collapsed = false, onToggle }: SidebarProps) {
  const { documentId } = useParams<{ documentId: string }>();
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("title");

  const {
    data: documents = [],
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["documents"],
    queryFn: fetchDocuments,
    staleTime: 30_000,
  });

  const sources = useMemo(() => {
    const set = new Set(documents.map((d) => d.source));
    return Array.from(set).sort();
  }, [documents]);

  const filtered = useMemo(() => {
    let docs = documents;

    if (sourceFilter !== "all") {
      docs = docs.filter((d) => d.source === sourceFilter);
    }

    if (search) {
      const q = search.toLowerCase();
      docs = docs.filter((d) => d.title.toLowerCase().includes(q));
    }

    docs = [...docs].sort((a, b) => {
      switch (sortKey) {
        case "title":
          return a.title.localeCompare(b.title);
        case "duration": {
          const da = a.metadata.duration_seconds ?? 0;
          const db = b.metadata.duration_seconds ?? 0;
          return db - da;
        }
        case "source":
          return a.source.localeCompare(b.source);
        default:
          return 0;
      }
    });

    return docs;
  }, [documents, sourceFilter, search, sortKey]);

  if (collapsed) {
    return (
      <div
        data-testid="sidebar"
        className={cn(
          "w-12 bg-surface-1 border-r border-white/5 flex flex-col items-center py-3 gap-2",
          className,
        )}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <Button data-testid="sidebar-toggle" variant="ghost" size="icon-sm" onClick={onToggle}>
              <PanelLeft className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">Expand sidebar</TooltipContent>
        </Tooltip>

        <Separator className="my-1" />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-sm" asChild>
              <Link to="/">
                <Tv2 className="h-4 w-4" />
              </Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">Media Library</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-sm" asChild>
              <Link to="/overrides">
                <ArrowRightLeft className="h-4 w-4" />
              </Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">Entity Overrides</TooltipContent>
        </Tooltip>
      </div>
    );
  }

  return (
    <div
      data-testid="sidebar"
      className={cn("w-72 bg-surface-1 border-r border-white/5 flex flex-col", className)}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-white/5">
        <Link
          to="/"
          className="text-sm font-semibold text-zinc-200 hover:text-white transition-colors flex items-center gap-2"
          style={{ fontFamily: "var(--font-display)" }}
        >
          <Tv2 className="h-4 w-4 text-cyan-400" />
          Media Explorer
        </Link>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button data-testid="sidebar-toggle" variant="ghost" size="icon-sm" onClick={onToggle}>
              <PanelLeftClose className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">Collapse sidebar</TooltipContent>
        </Tooltip>
      </div>

      {/* Nav links */}
      <div className="px-3 py-2 border-b border-white/5 flex items-center gap-1">
        <Button variant="ghost" size="sm" className="text-xs h-7" asChild>
          <Link to="/overrides">
            <ArrowRightLeft className="h-3.5 w-3.5 mr-1.5" />
            Entity Overrides
          </Link>
        </Button>
      </div>

      {/* Search + Filters */}
      <div className="p-2 space-y-2 border-b border-white/5">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500" />
          <Input
            type="text"
            placeholder="Search documents..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 h-8 text-xs bg-surface-2 border-white/5"
          />
        </div>

        <div className="flex gap-1.5">
          <Select value={sourceFilter} onValueChange={setSourceFilter}>
            <SelectTrigger className="flex-1 h-7 text-xs bg-surface-2 border-white/5">
              <SelectValue placeholder="All Sources" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Sources</SelectItem>
              {sources.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={sortKey} onValueChange={(v) => setSortKey(v as SortKey)}>
            <SelectTrigger className="w-24 h-7 text-xs bg-surface-2 border-white/5">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="title">A-Z</SelectItem>
              <SelectItem value="duration">Duration</SelectItem>
              <SelectItem value="source">Source</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Document list */}
      <ScrollArea className="flex-1">
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
          </div>
        )}

        {isError && <div className="p-3 text-sm text-red-400">Failed to load documents</div>}

        {!isLoading && filtered.length === 0 && (
          <div className="p-3 text-sm text-zinc-500">
            {search ? "No matching documents" : "No documents found"}
          </div>
        )}

        {filtered.map((doc) => (
          <DocumentListItem key={doc.id} doc={doc} isActive={doc.id === documentId} />
        ))}
      </ScrollArea>

      {/* Footer stats */}
      <div className="px-3 py-2 border-t border-white/5 text-[10px] text-zinc-600">
        {filtered.length} of {documents.length} documents
      </div>
    </div>
  );
}

function DocumentListItem({ doc, isActive }: { doc: MediaDocument; isActive: boolean }) {
  const duration = doc.metadata.duration_seconds;
  const ext = doc.metadata.extension?.toLowerCase() ?? "";
  const hasVideo = doc.metadata.has_video;

  return (
    <Link
      to={`/player/${encodeURIComponent(doc.id)}`}
      data-testid={`sidebar-item-${doc.id}`}
      className={cn(
        "block px-3 py-2 border-b border-white/[0.03] transition-colors",
        isActive
          ? "bg-white/[0.08] border-l-2 border-l-blue-500"
          : "hover:bg-white/[0.04] border-l-2 border-l-transparent",
      )}
    >
      <div className="flex items-start gap-2">
        <div className="mt-0.5 flex-shrink-0">
          {hasVideo ? (
            <Video className="h-3.5 w-3.5 text-zinc-500" />
          ) : (
            <AudioLines className="h-3.5 w-3.5 text-zinc-500" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p
            className={cn(
              "text-xs leading-tight truncate",
              isActive ? "text-zinc-100 font-medium" : "text-zinc-300",
            )}
          >
            {doc.title}
          </p>
          <div className="flex items-center gap-2 mt-0.5">
            <Badge variant="secondary" className="text-[9px] px-1 py-0 h-4 uppercase">
              {doc.source}
            </Badge>
            {duration != null && duration > 0 && (
              <span className="text-[10px] text-zinc-600 tabular-nums">{formatTime(duration)}</span>
            )}
            <span className="text-[10px] text-zinc-700 uppercase">{ext}</span>
          </div>
        </div>
      </div>
    </Link>
  );
}
