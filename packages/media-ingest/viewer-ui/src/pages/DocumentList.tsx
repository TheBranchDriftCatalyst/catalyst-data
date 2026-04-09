import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Button,
  Input,
  Badge,
  Card,
  CardContent,
  Separator,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Toggle,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  ScrollArea,
} from "@thebranchdriftcatalyst/catalyst-ui";
import {
  Search,
  LayoutGrid,
  List,
  Video,
  AudioLines,
  Clock,
  HardDrive,
  ArrowUpDown,
  FileQuestion,
  AlertCircle,
} from "lucide-react";
import { fetchDocuments } from "@/api/client";
import type { MediaDocument } from "@/types/media";
import { formatTime } from "@/lib/speakers";
import { formatBytes } from "@/lib/utils";
import { DocumentCardSkeleton, DocumentRowSkeleton } from "@/components/Skeleton";

type ViewMode = "grid" | "list";
type SortKey = "title" | "duration" | "size";

export default function DocumentList() {
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("title");

  const {
    data: documents = [],
    isLoading,
    isError,
    error,
    refetch,
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
        case "size":
          return (b.metadata.size_bytes ?? 0) - (a.metadata.size_bytes ?? 0);
        default:
          return 0;
      }
    });

    return docs;
  }, [documents, sourceFilter, search, sortKey]);

  // Stats
  const totalDuration = useMemo(
    () => documents.reduce((sum, d) => sum + (d.metadata.duration_seconds ?? 0), 0),
    [documents],
  );
  const totalSize = useMemo(
    () => documents.reduce((sum, d) => sum + (d.metadata.size_bytes ?? 0), 0),
    [documents],
  );

  return (
    <ScrollArea className="flex-1">
      <div className="p-6 max-w-[1800px] mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-zinc-100 tracking-tight">Media Library</h1>
          <p className="text-sm text-zinc-500 mt-1 flex items-center gap-3">
            <span>{documents.length} documents</span>
            {totalDuration > 0 && (
              <>
                <Separator orientation="vertical" className="h-3" />
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {formatTime(totalDuration)} total
                </span>
              </>
            )}
            {totalSize > 0 && (
              <>
                <Separator orientation="vertical" className="h-3" />
                <span className="flex items-center gap-1">
                  <HardDrive className="h-3 w-3" />
                  {formatBytes(totalSize)}
                </span>
              </>
            )}
          </p>
        </div>

        {/* Controls bar */}
        <div className="flex items-center gap-3 mb-6">
          {/* Search */}
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-500" />
            <Input
              type="text"
              placeholder="Search by title..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10 bg-surface-1 border-white/5"
            />
          </div>

          {/* Source filter pills */}
          <div className="flex gap-1.5">
            <Button
              variant={sourceFilter === "all" ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setSourceFilter("all")}
              className="text-xs h-8"
            >
              All
            </Button>
            {sources.map((s) => (
              <Button
                key={s}
                variant={sourceFilter === s ? "secondary" : "ghost"}
                size="sm"
                onClick={() => setSourceFilter(s)}
                className="text-xs h-8"
              >
                {s}
              </Button>
            ))}
          </div>

          {/* Sort */}
          <Select value={sortKey} onValueChange={(v) => setSortKey(v as SortKey)}>
            <SelectTrigger className="w-32 h-8 text-xs bg-surface-1 border-white/5">
              <ArrowUpDown className="h-3 w-3 mr-1.5" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="title">Name</SelectItem>
              <SelectItem value="duration">Duration</SelectItem>
              <SelectItem value="size">Size</SelectItem>
            </SelectContent>
          </Select>

          {/* View mode toggle */}
          <div className="flex bg-surface-1 rounded-md border border-white/5 p-0.5">
            <Tooltip>
              <TooltipTrigger asChild>
                <Toggle
                  pressed={viewMode === "grid"}
                  onPressedChange={() => setViewMode("grid")}
                  size="sm"
                  aria-label="Grid view"
                  className="h-7 w-7 p-0"
                >
                  <LayoutGrid className="h-3.5 w-3.5" />
                </Toggle>
              </TooltipTrigger>
              <TooltipContent>Grid view</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Toggle
                  pressed={viewMode === "list"}
                  onPressedChange={() => setViewMode("list")}
                  size="sm"
                  aria-label="List view"
                  className="h-7 w-7 p-0"
                >
                  <List className="h-3.5 w-3.5" />
                </Toggle>
              </TooltipTrigger>
              <TooltipContent>List view</TooltipContent>
            </Tooltip>
          </div>
        </div>

        {/* Loading */}
        {isLoading && (
          <>
            {viewMode === "grid" ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                {Array.from({ length: 8 }).map((_, i) => (
                  <DocumentCardSkeleton key={i} />
                ))}
              </div>
            ) : (
              <div className="space-y-1">
                {Array.from({ length: 8 }).map((_, i) => (
                  <DocumentRowSkeleton key={i} />
                ))}
              </div>
            )}
          </>
        )}

        {/* Error */}
        {isError && (
          <Card interactive={false} className="max-w-lg mx-auto mt-8">
            <CardContent className="flex flex-col items-center text-center py-8">
              <div className="rounded-full bg-red-950/50 p-3 mb-4">
                <AlertCircle className="h-6 w-6 text-red-400" />
              </div>
              <h3 className="text-sm font-medium text-zinc-200 mb-1">Failed to load documents</h3>
              <p className="text-xs text-zinc-500 mb-4">{(error as Error)?.message}</p>
              <Button variant="outline" size="sm" onClick={() => refetch()}>
                Try again
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Empty state */}
        {!isLoading && !isError && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-zinc-500">
            <FileQuestion className="h-12 w-12 mb-3 text-zinc-700" />
            {search ? (
              <p className="text-sm">No documents match &ldquo;{search}&rdquo;</p>
            ) : (
              <p className="text-sm">No documents found</p>
            )}
          </div>
        )}

        {/* Grid view */}
        {!isLoading && viewMode === "grid" && filtered.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filtered.map((doc) => (
              <DocumentCard key={doc.id} doc={doc} />
            ))}
          </div>
        )}

        {/* List view */}
        {!isLoading && viewMode === "list" && filtered.length > 0 && (
          <Card interactive={false} className="overflow-hidden">
            <div className="divide-y divide-white/[0.03]">
              {filtered.map((doc) => (
                <DocumentRow key={doc.id} doc={doc} />
              ))}
            </div>
          </Card>
        )}
      </div>
    </ScrollArea>
  );
}

function DocumentCard({ doc }: { doc: MediaDocument }) {
  const duration = doc.metadata.duration_seconds;
  const hasVideo = doc.metadata.has_video;
  const ext = doc.metadata.extension?.toLowerCase() ?? "";
  const sizeBytes = doc.metadata.size_bytes;

  return (
    <Link to={`/player/${encodeURIComponent(doc.id)}`} className="group block">
      <Card className="overflow-hidden h-full transition-all group-hover:border-white/15">
        {/* Thumbnail area */}
        <div className="relative h-32 bg-surface-2 flex items-center justify-center">
          {hasVideo ? (
            <Video className="h-10 w-10 text-zinc-600 group-hover:text-zinc-500 transition-colors" />
          ) : (
            <AudioLines className="h-10 w-10 text-zinc-600 group-hover:text-zinc-500 transition-colors" />
          )}
          {/* Duration badge */}
          {duration != null && duration > 0 && (
            <Badge
              variant="secondary"
              className="absolute bottom-2 right-2 text-[10px] px-1.5 py-0 font-mono tabular-nums"
            >
              {formatTime(duration)}
            </Badge>
          )}
          {/* Format badge */}
          <Badge
            variant="outline"
            className="absolute top-2 left-2 text-[10px] px-1.5 py-0 uppercase"
          >
            {ext}
          </Badge>
        </div>

        {/* Info */}
        <CardContent className="p-3">
          <h3 className="text-sm font-medium text-zinc-200 truncate group-hover:text-white transition-colors">
            {doc.title}
          </h3>
          <div className="flex items-center gap-2 mt-1.5">
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 uppercase">
              {doc.source}
            </Badge>
            {doc.metadata.video_codec && (
              <span className="text-[10px] text-zinc-600">{doc.metadata.video_codec}</span>
            )}
            {sizeBytes > 0 && (
              <span className="text-[10px] text-zinc-600">{formatBytes(sizeBytes)}</span>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

function DocumentRow({ doc }: { doc: MediaDocument }) {
  const duration = doc.metadata.duration_seconds;
  const hasVideo = doc.metadata.has_video;
  const ext = doc.metadata.extension?.toLowerCase() ?? "";
  const sizeBytes = doc.metadata.size_bytes;

  return (
    <Link
      to={`/player/${encodeURIComponent(doc.id)}`}
      className="group flex items-center gap-4 px-4 py-2.5 hover:bg-white/[0.04] transition-colors"
    >
      {/* Type icon */}
      <div className="flex-shrink-0">
        {hasVideo ? (
          <Video className="h-4 w-4 text-zinc-500" />
        ) : (
          <AudioLines className="h-4 w-4 text-zinc-500" />
        )}
      </div>

      {/* Title */}
      <div className="flex-1 min-w-0">
        <p className="text-sm text-zinc-300 truncate group-hover:text-zinc-100 transition-colors">
          {doc.title}
        </p>
      </div>

      {/* Metadata */}
      <Badge variant="secondary" className="text-[10px] px-1.5 py-0 uppercase flex-shrink-0">
        {doc.source}
      </Badge>
      <span className="text-[10px] text-zinc-600 uppercase flex-shrink-0 w-8 text-center">
        {ext}
      </span>
      {sizeBytes > 0 && (
        <span className="text-[10px] text-zinc-600 tabular-nums flex-shrink-0 w-16 text-right">
          {formatBytes(sizeBytes)}
        </span>
      )}
      {duration != null && duration > 0 && (
        <span className="text-xs text-zinc-500 tabular-nums flex-shrink-0 w-14 text-right font-mono">
          {formatTime(duration)}
        </span>
      )}
    </Link>
  );
}
