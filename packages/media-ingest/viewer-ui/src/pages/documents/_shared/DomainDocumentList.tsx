import { useState, useMemo, type ReactNode } from "react";
import { Link } from "react-router-dom";
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
  FileText,
  Clock,
  HardDrive,
  ArrowUpDown,
  FileQuestion,
  AlertCircle,
} from "lucide-react";
import type { Document } from "@/types/document";
import type { MediaDocument } from "@/types/media";
import { formatTime } from "@/lib/speakers";
import { formatBytes } from "@/lib/utils";
import { DocumentCardSkeleton, DocumentRowSkeleton } from "@/components/Skeleton";

type ViewMode = "grid" | "list";
type SortKey = "title" | "duration" | "size";

export interface DomainDocumentListProps<TDoc extends Document> {
  /** Domain slug, used in `data-testid` attributes so e2e tests can scope
   *  per-domain assertions. Also used to derive the default link target if
   *  `getHref` is not provided. */
  domainSlug: string;
  /** Heading text — defaults to humanized slug when omitted. */
  heading: string;
  documents: TDoc[];
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRefetch?: () => void;
  /** Per-card click target. Media-ingest routes to `/player/<id>`; other
   *  domains route to `/documents/<domain>/<id>` for the generic detail
   *  page. */
  getHref: (doc: TDoc) => string;
  /** Optional empty-state slot for when filters wash out all rows. */
  emptySlot?: ReactNode;
}

/** Generic, domain-agnostic documents list. Search by title, sort by
 *  title/duration/size, toggle grid/list. Cards adapt to whatever
 *  `metadata` is present — they never hard-require any single field. The
 *  per-domain wrapper picks the data fetcher and the click destination.
 */
export function DomainDocumentList<TDoc extends Document>({
  domainSlug,
  heading,
  documents,
  isLoading,
  isError,
  error,
  onRefetch,
  getHref,
  emptySlot,
}: DomainDocumentListProps<TDoc>) {
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("title");

  const sources = useMemo(() => {
    const set = new Set(documents.map((d) => d.source).filter(Boolean));
    return Array.from(set).sort();
  }, [documents]);

  const filtered = useMemo(() => {
    let docs = documents;
    if (sourceFilter !== "all") {
      docs = docs.filter((d) => d.source === sourceFilter);
    }
    if (search) {
      const q = search.toLowerCase();
      docs = docs.filter((d) => (d.title ?? "").toLowerCase().includes(q));
    }

    docs = [...docs].sort((a, b) => {
      switch (sortKey) {
        case "title":
          return (a.title ?? "").localeCompare(b.title ?? "");
        case "duration": {
          const da = (a.metadata?.duration_seconds as number | undefined) ?? 0;
          const db = (b.metadata?.duration_seconds as number | undefined) ?? 0;
          return db - da;
        }
        case "size": {
          const sa = (a.metadata?.size_bytes as number | undefined) ?? 0;
          const sb = (b.metadata?.size_bytes as number | undefined) ?? 0;
          return sb - sa;
        }
        default:
          return 0;
      }
    });

    return docs;
  }, [documents, sourceFilter, search, sortKey]);

  // Stats — only render when meaningful (audio/video metadata present).
  const totalDuration = useMemo(
    () =>
      documents.reduce(
        (sum, d) => sum + ((d.metadata?.duration_seconds as number | undefined) ?? 0),
        0,
      ),
    [documents],
  );
  const totalSize = useMemo(
    () =>
      documents.reduce((sum, d) => sum + ((d.metadata?.size_bytes as number | undefined) ?? 0), 0),
    [documents],
  );

  return (
    <ScrollArea className="flex-1">
      <div
        data-testid="document-list-page"
        data-domain={domainSlug}
        className="p-6 max-w-[1800px] mx-auto"
      >
        {/* Header */}
        <div className="mb-6">
          <h1
            className="text-2xl font-bold text-zinc-100 tracking-tight"
            style={{ fontFamily: "var(--font-display)" }}
          >
            {heading}
          </h1>
          <div className="text-sm text-zinc-500 mt-1 flex items-center gap-3">
            <span data-testid="document-count">{documents.length} documents</span>
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
          </div>
        </div>

        {/* Controls bar */}
        <div className="flex items-center gap-3 mb-6">
          {/* Search */}
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-500" />
            <Input
              data-testid="search-input"
              type="text"
              placeholder="Search by title..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10 bg-surface-1 border-white/5"
            />
          </div>

          {/* Source filter pills */}
          {sources.length > 0 && (
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
          )}

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
          <div data-testid="loading-skeleton">
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
          </div>
        )}

        {/* Error */}
        {isError && (
          <Card interactive={false} data-testid="error-state" className="max-w-lg mx-auto mt-8">
            <CardContent className="flex flex-col items-center text-center py-8">
              <div className="rounded-full bg-red-950/50 p-3 mb-4">
                <AlertCircle className="h-6 w-6 text-red-400" />
              </div>
              <h3 className="text-sm font-medium text-zinc-200 mb-1">Failed to load documents</h3>
              <p className="text-xs text-zinc-500 mb-4">{(error as Error)?.message}</p>
              {onRefetch && (
                <Button variant="outline" size="sm" onClick={onRefetch}>
                  Try again
                </Button>
              )}
            </CardContent>
          </Card>
        )}

        {/* Empty state */}
        {!isLoading && !isError && filtered.length === 0 && (
          <div
            data-testid="empty-state"
            className="flex flex-col items-center justify-center py-16 text-zinc-500"
          >
            <FileQuestion className="h-12 w-12 mb-3 text-zinc-700" />
            {search ? (
              <p className="text-sm">No documents match &ldquo;{search}&rdquo;</p>
            ) : (
              (emptySlot ?? <p className="text-sm">No documents found</p>)
            )}
          </div>
        )}

        {/* Grid view */}
        {!isLoading && viewMode === "grid" && filtered.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filtered.map((doc) => (
              <DocumentCard key={doc.id} doc={doc} href={getHref(doc)} />
            ))}
          </div>
        )}

        {/* List view */}
        {!isLoading && viewMode === "list" && filtered.length > 0 && (
          <Card interactive={false} className="overflow-hidden">
            <div className="divide-y divide-white/[0.03]">
              {filtered.map((doc) => (
                <DocumentRow key={doc.id} doc={doc} href={getHref(doc)} />
              ))}
            </div>
          </Card>
        )}
      </div>
    </ScrollArea>
  );
}

// ── Card / row primitives — driven entirely by `doc.metadata`.

function getDocFlags(doc: Document) {
  const meta = doc.metadata ?? {};
  // Cast through `MediaDocument["metadata"]` so the optional fields type-check
  // when `doc` has the richer media shape. Generic Document stays compatible
  // because every accessor is `?.` or coerced to a default.
  const m = meta as MediaDocument["metadata"];
  return {
    duration: m.duration_seconds,
    hasVideo: Boolean(m.has_video),
    hasAudio: Boolean(m.has_audio),
    ext: (m.extension ?? "").toLowerCase(),
    sizeBytes: m.size_bytes ?? 0,
    videoCodec: m.video_codec,
    thumbnailUrl: (doc as MediaDocument).thumbnail_url,
  };
}

function DocumentCard({ doc, href }: { doc: Document; href: string }) {
  const { duration, hasVideo, hasAudio, ext, sizeBytes, videoCodec, thumbnailUrl } =
    getDocFlags(doc);
  const Icon = hasVideo ? Video : hasAudio ? AudioLines : FileText;

  return (
    <Link to={href} data-testid={`document-card-${doc.id}`} className="group block">
      <Card className="overflow-hidden h-full transition-all group-hover:border-white/15 group-hover:shadow-lg group-hover:shadow-black/20">
        {/* Thumbnail area */}
        <div className="relative h-36 bg-gradient-to-br from-surface-2 to-surface-0 flex items-center justify-center overflow-hidden">
          {thumbnailUrl ? (
            <img
              src={thumbnailUrl}
              alt=""
              loading="lazy"
              className="absolute inset-0 w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
          ) : (
            <>
              <div
                className="absolute inset-0 opacity-[0.04]"
                style={{
                  backgroundImage:
                    "linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)",
                  backgroundSize: "24px 24px",
                }}
              />
              <Icon className="h-8 w-8 text-zinc-700 group-hover:text-zinc-500 transition-colors relative z-10" />
            </>
          )}
          <div className="absolute bottom-0 left-0 right-0 h-12 bg-gradient-to-t from-surface-0/80 to-transparent" />
          {duration != null && duration > 0 && (
            <Badge
              variant="secondary"
              className="absolute bottom-2 right-2 text-[10px] px-1.5 py-0 font-mono tabular-nums bg-black/60 backdrop-blur-sm border-white/10 z-10"
            >
              {formatTime(duration)}
            </Badge>
          )}
          {ext && (
            <Badge
              variant="outline"
              className="absolute top-2 left-2 text-[10px] px-1.5 py-0 uppercase bg-black/40 backdrop-blur-sm border-white/10 z-10"
            >
              {ext}
            </Badge>
          )}
        </div>

        <CardContent className="p-3">
          <h3 className="text-sm font-medium text-zinc-200 truncate group-hover:text-white transition-colors leading-snug">
            {doc.title || doc.id}
          </h3>
          <div className="flex items-center gap-2 mt-2">
            {doc.source && (
              <Badge
                variant="secondary"
                className="text-[9px] px-1.5 py-0 uppercase tracking-wider"
              >
                {doc.source}
              </Badge>
            )}
            {videoCodec && (
              <span className="text-[10px] text-zinc-600 font-mono">{videoCodec}</span>
            )}
            {sizeBytes > 0 && (
              <span className="text-[10px] text-zinc-600 font-mono">{formatBytes(sizeBytes)}</span>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

function DocumentRow({ doc, href }: { doc: Document; href: string }) {
  const { duration, hasVideo, hasAudio, ext, sizeBytes } = getDocFlags(doc);
  const Icon = hasVideo ? Video : hasAudio ? AudioLines : FileText;

  return (
    <Link
      to={href}
      data-testid={`document-row-${doc.id}`}
      className="group flex items-center gap-4 px-4 py-2.5 hover:bg-white/[0.04] transition-colors"
    >
      <div className="flex-shrink-0">
        <Icon className="h-4 w-4 text-zinc-500" />
      </div>

      <div className="flex-1 min-w-0">
        <p className="text-sm text-zinc-300 truncate group-hover:text-zinc-100 transition-colors">
          {doc.title || doc.id}
        </p>
      </div>

      {doc.source && (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 uppercase flex-shrink-0">
          {doc.source}
        </Badge>
      )}
      {ext && (
        <span className="text-[10px] text-zinc-600 uppercase flex-shrink-0 w-8 text-center">
          {ext}
        </span>
      )}
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
