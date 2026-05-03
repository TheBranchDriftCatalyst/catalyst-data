import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge, Button, Input } from "@thebranchdriftcatalyst/catalyst-ui";
import {
  ArrowLeft,
  ArrowUpDown,
  ChevronRight,
  Database,
  Globe2,
  Keyboard,
  Search,
  X,
} from "lucide-react";
import {
  fetchS3FolderStats,
  fetchS3List,
  fetchS3Search,
  type S3File,
  type S3Folder,
  type S3SearchHit,
} from "@/api/client";
import { cn } from "@/lib/utils";
import { HotkeysOverlay } from "./s3/HotkeysOverlay";
import { Listing } from "./s3/Listing";
import { PinnedRail } from "./s3/PinnedRail";
import { Preview } from "./s3/Preview";
import { SearchResults } from "./s3/SearchResults";
import { useDebouncedValue } from "./s3/useDebouncedValue";
import { useExplorerState, type SortKey } from "./s3/useExplorerState";
import { useRecentPrefixes } from "./s3/useRecentPrefixes";
import { formatBytes, formatDate, layerColor, sortListing } from "./s3/utils";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "name", label: "Name" },
  { value: "size", label: "Size" },
  { value: "modified", label: "Modified" },
];

export default function S3Explorer() {
  const state = useExplorerState();
  const { recent } = useRecentPrefixes(state.prefix);

  const [pathInput, setPathInput] = useState("");
  const [hotkeysOpen, setHotkeysOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const pathInputRef = useRef<HTMLInputElement>(null);

  const debouncedQuery = useDebouncedValue(state.query.trim(), 80);
  const isSearching = debouncedQuery.length > 0;
  const searchScopePrefix = state.scope === "bucket" ? "" : state.prefix;

  // ── data ────────────────────────────────────────────────────────────────

  const listingQ = useQuery({
    queryKey: ["s3-list", state.prefix],
    queryFn: () => fetchS3List(state.prefix),
    staleTime: 30_000,
    enabled: !isSearching,
  });

  // Stats are fetched separately so the listing renders instantly. The
  // backend computes them in a daemon thread on cache miss; we poll until
  // it returns "ready". 120s cache → typical second visit is free.
  const statsQ = useQuery({
    queryKey: ["s3-folder-stats", state.prefix],
    queryFn: () => fetchS3FolderStats(state.prefix),
    enabled: !isSearching,
    staleTime: 60_000,
    refetchInterval: (query) => (query.state.data?.status === "computing" ? 1500 : false),
  });
  const statsReady = statsQ.data?.status === "ready" ? statsQ.data : null;

  const searchQ = useQuery({
    queryKey: ["s3-search", debouncedQuery, searchScopePrefix],
    queryFn: () => fetchS3Search(debouncedQuery, searchScopePrefix),
    staleTime: 30_000,
    enabled: isSearching,
  });

  const sortedListing = useMemo(() => {
    if (!listingQ.data) return null;
    // Merge per-folder stats onto each folder once they're ready. Done
    // here (not in the query selector) so the table re-renders the moment
    // stats land without an extra network round-trip.
    const enrichedFolders: S3Folder[] = statsReady
      ? listingQ.data.folders.map((f) => {
          const s = statsReady.folder_stats[f.prefix];
          return s ? { ...f, ...s } : f;
        })
      : listingQ.data.folders;
    return sortListing(enrichedFolders, listingQ.data.files, state.sort, state.sortDesc);
  }, [listingQ.data, statsReady, state.sort, state.sortDesc]);

  const totalRows = isSearching
    ? (searchQ.data?.hits.length ?? 0)
    : sortedListing
      ? sortedListing.folders.length + sortedListing.files.length
      : 0;

  // Reset cursor when the visible set changes shape.
  useEffect(() => {
    setCursor(0);
  }, [debouncedQuery, state.prefix, state.scope]);

  // ── selected file (lookup so Preview has metadata) ──────────────────────

  const selectedFile: S3File | null = useMemo(() => {
    if (!state.selectedKey) return null;
    if (sortedListing) {
      const hit = sortedListing.files.find((f) => f.key === state.selectedKey);
      if (hit) return hit;
    }
    if (searchQ.data) {
      const hit = searchQ.data.hits.find((h) => h.key === state.selectedKey);
      if (hit) {
        return {
          key: hit.key,
          name: hit.name,
          size: hit.size,
          last_modified: hit.last_modified,
        };
      }
    }
    // Fall back to a synthetic record so deep-linked URLs still render.
    return {
      key: state.selectedKey,
      name: state.selectedKey.split("/").pop() ?? state.selectedKey,
      size: 0,
      last_modified: new Date().toISOString(),
    };
  }, [state.selectedKey, sortedListing, searchQ.data]);

  // ── navigation primitives ──────────────────────────────────────────────

  const navigate = useCallback(
    (newPrefix: string) => {
      // setPrefix already bundles clearing `key` and `q` into one URL update,
      // which avoids the stale-prev race we'd hit with two consecutive
      // setSearchParams calls.
      state.setPrefix(newPrefix);
    },
    [state],
  );

  const goUp = useCallback(() => {
    const parts = state.prefix.replace(/\/$/, "").split("/");
    parts.pop();
    navigate(parts.length > 0 ? parts.join("/") + "/" : "");
  }, [state.prefix, navigate]);

  const jumpToPath = useCallback(() => {
    const p = pathInput.trim();
    if (!p) return;
    navigate(p.endsWith("/") ? p : p + "/");
    setPathInput("");
  }, [pathInput, navigate]);

  const activateRow = useCallback(
    (rowIndex: number) => {
      if (isSearching) {
        const hit = searchQ.data?.hits[rowIndex];
        if (!hit) return;
        if (hit.key.endsWith("/")) {
          navigate(hit.key);
        } else {
          state.setSelectedKey(hit.key);
        }
        return;
      }
      if (!sortedListing) return;
      if (rowIndex < sortedListing.folders.length) {
        const folder = sortedListing.folders[rowIndex];
        if (folder) navigate(folder.prefix);
      } else {
        const file = sortedListing.files[rowIndex - sortedListing.folders.length];
        if (file) state.setSelectedKey(file.key);
      }
    },
    [isSearching, searchQ.data, sortedListing, navigate, state],
  );

  // ── keyboard ───────────────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inEditable =
        target &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);

      // Global focus hotkeys (work even from inputs).
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
        return;
      }

      if (e.key === "Escape") {
        if (state.query) {
          state.setQuery("");
          searchInputRef.current?.blur();
        } else if (state.selectedKey) {
          state.setSelectedKey(null);
        } else if (hotkeysOpen) {
          setHotkeysOpen(false);
        }
        return;
      }

      // Don't hijack keystrokes while the user is typing in an input.
      if (inEditable && target !== searchInputRef.current) return;

      if (target === searchInputRef.current) {
        // Within the search input we still allow arrow-key cursor movement
        // through results.
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setCursor((c) => Math.min(totalRows - 1, c + 1));
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          setCursor((c) => Math.max(0, c - 1));
        } else if (e.key === "Enter") {
          e.preventDefault();
          activateRow(cursor);
        }
        return;
      }

      // `?` may arrive as "?" (mac) or "/"+shift (some Playwright/linux paths).
      const isQuestion = e.key === "?" || (e.key === "/" && e.shiftKey);
      if (isQuestion) {
        e.preventDefault();
        setHotkeysOpen((v) => !v);
      } else if (e.key === "/") {
        e.preventDefault();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setCursor((c) => Math.min(totalRows - 1, c + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setCursor((c) => Math.max(0, c - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        activateRow(cursor);
      } else if (e.key === "u" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        goUp();
      } else if (e.key === "g" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        navigate("");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [state, totalRows, cursor, activateRow, goUp, navigate, hotkeysOpen]);

  // ── breadcrumbs ────────────────────────────────────────────────────────

  const breadcrumbs = state.prefix
    .split("/")
    .filter(Boolean)
    .map((part, i, arr) => ({
      label: part,
      prefix: arr.slice(0, i + 1).join("/") + "/",
    }));

  // ── render ─────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full" data-testid="s3-explorer">
      <PinnedRail currentPrefix={state.prefix} recent={recent} onNavigate={navigate} />

      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="flex-shrink-0 px-4 py-3 border-b border-white/5 space-y-2">
          <div className="flex items-center gap-3">
            <Database className="h-5 w-5 text-cyan-400" />
            <h1 className="text-lg font-semibold">S3 Explorer</h1>
            <Badge variant="secondary" className="text-[10px]">
              {isSearching
                ? `${searchQ.data?.total ?? 0} matches`
                : sortedListing
                  ? `${sortedListing.folders.length} folders, ${sortedListing.files.length} files`
                  : "..."}
            </Badge>
            {!isSearching && statsReady && (
              <span data-testid="s3-prefix-stats" className="text-[10px] text-zinc-500 font-mono">
                {statsReady.prefix_stats.file_count} keys &middot;{" "}
                {formatBytes(statsReady.prefix_stats.total_size)}
                {statsReady.prefix_stats.last_modified && (
                  <> &middot; updated {formatDate(statsReady.prefix_stats.last_modified)}</>
                )}
              </span>
            )}
            {!isSearching && statsQ.data?.status === "computing" && (
              <span
                data-testid="s3-stats-computing"
                className="text-[10px] text-zinc-600 font-mono italic"
              >
                computing stats…
              </span>
            )}
            {!isSearching && listingQ.data?.truncated && (
              <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-400">
                listing truncated — refine via search
              </Badge>
            )}

            <div className="flex-1" />

            <SortMenu sort={state.sort} desc={state.sortDesc} onChange={state.setSort} />
            <Button
              variant="ghost"
              size="icon-sm"
              title="Keyboard shortcuts (?)"
              onClick={() => setHotkeysOpen(true)}
            >
              <Keyboard className="h-3.5 w-3.5" />
            </Button>
          </div>

          {/* Breadcrumbs */}
          <div className="flex items-center gap-1 text-xs">
            <button
              onClick={() => navigate("")}
              className={cn(
                "hover:text-white transition-colors px-1 py-0.5 rounded",
                state.prefix === "" ? "text-cyan-400 font-medium" : "text-zinc-400",
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
                    bc.prefix === state.prefix
                      ? "text-cyan-400 font-medium"
                      : (layerColor(bc.label) ?? "text-zinc-400"),
                  )}
                >
                  {bc.label}
                </button>
              </span>
            ))}
          </div>

          {/* Search + path jump */}
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500" />
              <Input
                ref={searchInputRef}
                data-testid="s3-search-input"
                placeholder="Search keys (press / or ⌘K)"
                value={state.query}
                onChange={(e) => state.setQuery(e.target.value)}
                className="h-7 pl-7 pr-20 text-xs bg-surface-2 border-white/5"
              />
              <button
                onClick={state.toggleScope}
                title={
                  state.scope === "prefix"
                    ? `Searching within ${state.prefix || "/"} — click for whole bucket`
                    : "Searching whole bucket — click to scope to current prefix"
                }
                className={cn(
                  "absolute right-7 top-1/2 -translate-y-1/2 h-5 px-1.5 rounded text-[10px] font-mono flex items-center gap-1 transition-colors",
                  state.scope === "bucket"
                    ? "bg-cyan-500/15 text-cyan-300 hover:bg-cyan-500/25"
                    : "text-zinc-500 hover:text-zinc-300 hover:bg-white/5",
                )}
              >
                <Globe2 className="h-3 w-3" />
                {state.scope === "bucket" ? "all" : "here"}
              </button>
              {state.query && (
                <button
                  onClick={() => state.setQuery("")}
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-5 w-5 rounded text-zinc-500 hover:text-zinc-300 flex items-center justify-center"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>

            <Input
              ref={pathInputRef}
              data-testid="s3-path-input"
              placeholder="Jump to path"
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && jumpToPath()}
              className="w-64 h-7 text-xs bg-surface-2 border-white/5"
            />
            {state.prefix && (
              <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={goUp}>
                <ArrowLeft className="h-3.5 w-3.5 mr-1" />
                Up
              </Button>
            )}
          </div>
        </div>

        {/* Body: listing | preview */}
        <div className="flex-1 flex min-h-0">
          <div
            data-testid="s3-listing"
            className={cn(
              "flex flex-col border-r border-white/5",
              state.selectedKey ? "w-[42%] min-w-[320px]" : "flex-1",
            )}
          >
            {isSearching ? (
              <SearchPanel
                loading={searchQ.isLoading}
                error={searchQ.isError}
                hits={searchQ.data?.hits ?? []}
                total={searchQ.data?.total ?? 0}
                cursor={cursor}
                selectedKey={state.selectedKey}
                onActivate={(hit: S3SearchHit) => {
                  if (hit.key.endsWith("/")) navigate(hit.key);
                  else state.setSelectedKey(hit.key);
                }}
                onHover={setCursor}
              />
            ) : (
              <ListingPanel
                loading={listingQ.isLoading}
                error={listingQ.isError}
                folders={sortedListing?.folders ?? []}
                files={sortedListing?.files ?? []}
                cursor={cursor}
                selectedKey={state.selectedKey}
                onNavigate={navigate}
                onSelectFile={(f) => state.setSelectedKey(f.key)}
                onHover={setCursor}
              />
            )}
          </div>

          {selectedFile && (
            <Preview file={selectedFile} onClose={() => state.setSelectedKey(null)} />
          )}
        </div>
      </div>

      <HotkeysOverlay open={hotkeysOpen} onClose={() => setHotkeysOpen(false)} />
    </div>
  );
}

// ── small panel wrappers (loading / error states) ────────────────────────

function ListingPanel({
  loading,
  error,
  folders,
  files,
  cursor,
  selectedKey,
  onNavigate,
  onSelectFile,
  onHover,
}: {
  loading: boolean;
  error: boolean;
  folders: ReturnType<typeof sortListing>["folders"];
  files: ReturnType<typeof sortListing>["files"];
  cursor: number;
  selectedKey: string | null;
  onNavigate: (p: string) => void;
  onSelectFile: (f: S3File) => void;
  onHover: (i: number) => void;
}) {
  if (loading) return <PanelSpinner />;
  if (error) return <div className="p-4 text-sm text-red-400">Failed to list objects</div>;
  return (
    <Listing
      folders={folders}
      files={files}
      highlightedIndex={cursor}
      selectedKey={selectedKey}
      onNavigate={onNavigate}
      onSelectFile={onSelectFile}
      onHover={onHover}
    />
  );
}

function SearchPanel({
  loading,
  error,
  hits,
  total,
  cursor,
  selectedKey,
  onActivate,
  onHover,
}: {
  loading: boolean;
  error: boolean;
  hits: S3SearchHit[];
  total: number;
  cursor: number;
  selectedKey: string | null;
  onActivate: (hit: S3SearchHit) => void;
  onHover: (i: number) => void;
}) {
  if (loading) return <PanelSpinner />;
  if (error) return <div className="p-4 text-sm text-red-400">Search failed</div>;
  return (
    <SearchResults
      hits={hits}
      total={total}
      truncated={hits.length < total}
      highlightedIndex={cursor}
      selectedKey={selectedKey}
      onActivate={onActivate}
      onHover={onHover}
    />
  );
}

function PanelSpinner() {
  return (
    <div className="flex items-center justify-center py-8">
      <div className="w-5 h-5 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
    </div>
  );
}

// ── sort menu ─────────────────────────────────────────────────────────────

function SortMenu({
  sort,
  desc,
  onChange,
}: {
  sort: SortKey;
  desc: boolean;
  onChange: (s: SortKey, desc?: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-1 text-[10px] font-mono">
      <ArrowUpDown className="h-3 w-3 text-zinc-500" />
      {SORT_OPTIONS.map((opt) => {
        const active = opt.value === sort;
        return (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value, active ? !desc : false)}
            className={cn(
              "px-1.5 py-0.5 rounded transition-colors",
              active ? "bg-white/[0.06] text-cyan-300" : "text-zinc-500 hover:text-zinc-300",
            )}
          >
            {opt.label}
            {active && (desc ? " ↓" : " ↑")}
          </button>
        );
      })}
    </div>
  );
}
