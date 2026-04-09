import { useState, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchDocuments } from "@/api/client";
import type { MediaDocument } from "@/types/media";
import { formatTime } from "@/lib/speakers";

type SortKey = "title" | "duration" | "source";

interface SidebarProps {
  className?: string;
  collapsed?: boolean;
  onToggle?: () => void;
}

export default function Sidebar({
  className = "",
  collapsed = false,
  onToggle,
}: SidebarProps) {
  const { documentId } = useParams<{ documentId: string }>();
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("title");

  const { data: documents = [], isLoading, isError } = useQuery({
    queryKey: ["documents"],
    queryFn: fetchDocuments,
    staleTime: 30_000,
  });

  // Get unique sources
  const sources = useMemo(() => {
    const set = new Set(documents.map((d) => d.source));
    return Array.from(set).sort();
  }, [documents]);

  // Filter + sort
  const filtered = useMemo(() => {
    let docs = documents;

    // Source filter
    if (sourceFilter !== "all") {
      docs = docs.filter((d) => d.source === sourceFilter);
    }

    // Text search
    if (search) {
      const q = search.toLowerCase();
      docs = docs.filter((d) => d.title.toLowerCase().includes(q));
    }

    // Sort
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
      <div className={`w-10 bg-surface-1 border-r border-white/5 flex flex-col items-center py-3 ${className}`}>
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md hover:bg-surface-2 text-zinc-400 hover:text-zinc-200 transition-colors"
          title="Expand sidebar"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
          </svg>
        </button>
      </div>
    );
  }

  return (
    <div className={`w-72 bg-surface-1 border-r border-white/5 flex flex-col ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-white/5">
        <Link to="/" className="text-sm font-semibold text-zinc-200 hover:text-white transition-colors">
          Media Viewer
        </Link>
        <button
          onClick={onToggle}
          className="p-1 rounded hover:bg-surface-2 text-zinc-500 hover:text-zinc-300 transition-colors"
          title="Collapse sidebar"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
          </svg>
        </button>
      </div>

      {/* Search */}
      <div className="p-2 space-y-2 border-b border-white/5">
        <div className="relative">
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500"
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
            placeholder="Search documents..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-surface-2 text-sm text-zinc-300 placeholder-zinc-600 rounded-md pl-8 pr-3 py-1.5 border border-white/5 focus:outline-none focus:border-white/20 transition-colors"
          />
        </div>

        {/* Filters row */}
        <div className="flex gap-1.5">
          {/* Source filter */}
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="flex-1 bg-surface-2 text-xs text-zinc-400 rounded-md px-2 py-1 border border-white/5 focus:outline-none focus:border-white/20"
          >
            <option value="all">All Sources</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>

          {/* Sort */}
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="bg-surface-2 text-xs text-zinc-400 rounded-md px-2 py-1 border border-white/5 focus:outline-none focus:border-white/20"
          >
            <option value="title">A-Z</option>
            <option value="duration">Duration</option>
            <option value="source">Source</option>
          </select>
        </div>
      </div>

      {/* Document list */}
      <div className="flex-1 overflow-y-auto">
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
          </div>
        )}

        {isError && (
          <div className="p-3 text-sm text-red-400">Failed to load documents</div>
        )}

        {!isLoading && filtered.length === 0 && (
          <div className="p-3 text-sm text-zinc-500">
            {search ? "No matching documents" : "No documents found"}
          </div>
        )}

        {filtered.map((doc) => (
          <DocumentListItem
            key={doc.id}
            doc={doc}
            isActive={doc.id === documentId}
          />
        ))}
      </div>

      {/* Footer stats */}
      <div className="px-3 py-2 border-t border-white/5 text-[10px] text-zinc-600">
        {filtered.length} of {documents.length} documents
      </div>
    </div>
  );
}

function DocumentListItem({
  doc,
  isActive,
}: {
  doc: MediaDocument;
  isActive: boolean;
}) {
  const duration = doc.metadata.duration_seconds;
  const ext = doc.metadata.extension?.toLowerCase() ?? "";
  const hasVideo = doc.metadata.has_video;

  return (
    <Link
      to={`/player/${encodeURIComponent(doc.id)}`}
      className={`
        block px-3 py-2 border-b border-white/[0.03] transition-colors
        ${isActive
          ? "bg-white/[0.08] border-l-2 border-l-blue-500"
          : "hover:bg-white/[0.04] border-l-2 border-l-transparent"
        }
      `}
    >
      <div className="flex items-start gap-2">
        {/* Media type icon */}
        <div className="mt-0.5 flex-shrink-0">
          {hasVideo ? (
            <svg className="w-3.5 h-3.5 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z" />
            </svg>
          ) : (
            <svg className="w-3.5 h-3.5 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
            </svg>
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p className={`text-xs leading-tight truncate ${isActive ? "text-zinc-100 font-medium" : "text-zinc-300"}`}>
            {doc.title}
          </p>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[10px] text-zinc-600 uppercase">{doc.source}</span>
            {duration != null && duration > 0 && (
              <span className="text-[10px] text-zinc-600 tabular-nums">
                {formatTime(duration)}
              </span>
            )}
            <span className="text-[10px] text-zinc-700">{ext}</span>
          </div>
        </div>
      </div>
    </Link>
  );
}
