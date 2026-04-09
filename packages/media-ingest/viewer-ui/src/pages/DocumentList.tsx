import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchDocuments } from "@/api/client";
import type { MediaDocument } from "@/types/media";
import { formatTime } from "@/lib/speakers";

type ViewMode = "grid" | "list";

export default function DocumentList() {
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("all");

  const { data: documents = [], isLoading, isError, error } = useQuery({
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
    return docs;
  }, [documents, sourceFilter, search]);

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-zinc-100">Media Library</h1>
        <p className="text-sm text-zinc-500 mt-1">
          {documents.length} documents indexed
        </p>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-3 mb-6">
        {/* Search */}
        <div className="relative flex-1 max-w-md">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
            />
          </svg>
          <input
            type="text"
            placeholder="Search by title..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-surface-1 text-sm text-zinc-300 placeholder-zinc-600 rounded-lg pl-10 pr-4 py-2.5 border border-white/5 focus:outline-none focus:border-white/20 transition-colors"
          />
        </div>

        {/* Source filter pills */}
        <div className="flex gap-1.5">
          <button
            onClick={() => setSourceFilter("all")}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              sourceFilter === "all"
                ? "bg-white/10 text-zinc-200"
                : "bg-surface-1 text-zinc-500 hover:text-zinc-300 hover:bg-surface-2"
            }`}
          >
            All
          </button>
          {sources.map((s) => (
            <button
              key={s}
              onClick={() => setSourceFilter(s)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                sourceFilter === s
                  ? "bg-white/10 text-zinc-200"
                  : "bg-surface-1 text-zinc-500 hover:text-zinc-300 hover:bg-surface-2"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        {/* View mode toggle */}
        <div className="flex bg-surface-1 rounded-md border border-white/5 p-0.5">
          <button
            onClick={() => setViewMode("grid")}
            className={`p-1.5 rounded transition-colors ${
              viewMode === "grid" ? "bg-surface-2 text-zinc-200" : "text-zinc-500 hover:text-zinc-300"
            }`}
            title="Grid view"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
            </svg>
          </button>
          <button
            onClick={() => setViewMode("list")}
            className={`p-1.5 rounded transition-colors ${
              viewMode === "list" ? "bg-surface-2 text-zinc-200" : "text-zinc-500 hover:text-zinc-300"
            }`}
            title="List view"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM3.75 12h.007v.008H3.75V12zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm-.375 5.25h.007v.008H3.75v-.008zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
            </svg>
          </button>
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-16">
          <div className="flex items-center gap-3 text-zinc-400">
            <div className="w-6 h-6 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
            <span className="text-sm">Loading documents...</span>
          </div>
        </div>
      )}

      {/* Error */}
      {isError && (
        <div className="rounded-lg bg-red-950/30 border border-red-900/50 p-4 text-sm text-red-300">
          <p className="font-medium">Failed to load documents</p>
          <p className="text-red-400/70 mt-1">{(error as Error)?.message}</p>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !isError && filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-zinc-500">
          <svg className="w-12 h-12 mb-3 text-zinc-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m5.231 13.481L15 17.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v16.5c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9zm3.75 11.625a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
          </svg>
          {search ? (
            <p className="text-sm">No documents match "{search}"</p>
          ) : (
            <p className="text-sm">No documents found</p>
          )}
        </div>
      )}

      {/* Grid view */}
      {viewMode === "grid" && filtered.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filtered.map((doc) => (
            <DocumentCard key={doc.id} doc={doc} />
          ))}
        </div>
      )}

      {/* List view */}
      {viewMode === "list" && filtered.length > 0 && (
        <div className="space-y-1">
          {filtered.map((doc) => (
            <DocumentRow key={doc.id} doc={doc} />
          ))}
        </div>
      )}
    </div>
  );
}

function DocumentCard({ doc }: { doc: MediaDocument }) {
  const duration = doc.metadata.duration_seconds;
  const hasVideo = doc.metadata.has_video;
  const ext = doc.metadata.extension?.toLowerCase() ?? "";
  const sizeBytes = doc.metadata.size_bytes;

  return (
    <Link
      to={`/player/${encodeURIComponent(doc.id)}`}
      className="group block bg-surface-1 rounded-lg border border-white/5 overflow-hidden hover:border-white/10 hover:bg-surface-2/50 transition-all"
    >
      {/* Thumbnail area */}
      <div className="relative h-32 bg-surface-2 flex items-center justify-center">
        {hasVideo ? (
          <svg className="w-10 h-10 text-zinc-600 group-hover:text-zinc-500 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
            <path strokeLinecap="round" strokeLinejoin="round" d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z" />
          </svg>
        ) : (
          <svg className="w-10 h-10 text-zinc-600 group-hover:text-zinc-500 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
          </svg>
        )}
        {/* Duration badge */}
        {duration != null && duration > 0 && (
          <span className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/70 text-[10px] font-medium text-zinc-200 tabular-nums">
            {formatTime(duration)}
          </span>
        )}
        {/* Format badge */}
        <span className="absolute top-2 left-2 px-1.5 py-0.5 rounded bg-black/50 text-[10px] text-zinc-400 uppercase">
          {ext}
        </span>
      </div>

      {/* Info */}
      <div className="p-3">
        <h3 className="text-sm font-medium text-zinc-200 truncate group-hover:text-white transition-colors">
          {doc.title}
        </h3>
        <div className="flex items-center gap-2 mt-1.5">
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-zinc-500 uppercase font-medium">
            {doc.source}
          </span>
          {doc.metadata.video_codec && (
            <span className="text-[10px] text-zinc-600">{doc.metadata.video_codec}</span>
          )}
          {sizeBytes > 0 && (
            <span className="text-[10px] text-zinc-600">{formatBytes(sizeBytes)}</span>
          )}
        </div>
      </div>
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
      className="group flex items-center gap-4 px-4 py-2.5 rounded-lg hover:bg-surface-1 transition-colors"
    >
      {/* Type icon */}
      <div className="flex-shrink-0">
        {hasVideo ? (
          <svg className="w-5 h-5 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z" />
          </svg>
        ) : (
          <svg className="w-5 h-5 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
          </svg>
        )}
      </div>

      {/* Title */}
      <div className="flex-1 min-w-0">
        <p className="text-sm text-zinc-300 truncate group-hover:text-zinc-100 transition-colors">
          {doc.title}
        </p>
      </div>

      {/* Metadata */}
      <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-zinc-500 uppercase font-medium flex-shrink-0">
        {doc.source}
      </span>
      <span className="text-[10px] text-zinc-600 uppercase flex-shrink-0 w-8 text-center">
        {ext}
      </span>
      {sizeBytes > 0 && (
        <span className="text-[10px] text-zinc-600 tabular-nums flex-shrink-0 w-16 text-right">
          {formatBytes(sizeBytes)}
        </span>
      )}
      {duration != null && duration > 0 && (
        <span className="text-xs text-zinc-500 tabular-nums flex-shrink-0 w-14 text-right">
          {formatTime(duration)}
        </span>
      )}
    </Link>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
