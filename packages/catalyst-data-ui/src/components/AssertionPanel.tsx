import { useState, useMemo, useCallback } from "react";
import { Input, ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { Search, MessageSquareQuote, Layers, List } from "lucide-react";
import type { Assertion, AnnotationStatus } from "@/types/media";
import { AssertionCard, AnnotationControls, type StatusFilter } from "./domain";
import { lookupFrame } from "@/data/amrFrames";
import { cn } from "@/lib/utils";

interface AssertionPanelProps {
  assertions: Assertion[];
  onAssertionSelect?: (assertionId: string | null) => void;
  selectedAssertionId?: string | null;
  /** Called to seek the video to an assertion's timestamp (seconds). */
  onSeek?: (timeInSeconds: number) => void;
  /** Annotation helpers — optional; panel works without them. */
  getStatus?: (targetId: string) => AnnotationStatus;
  onApprove?: (targetId: string) => void;
  onReject?: (targetId: string) => void;
  onBulkApprove?: (items: { targetType: "assertion"; targetId: string }[]) => void;
  onBulkReject?: (items: { targetType: "assertion"; targetId: string }[]) => void;
  className?: string;
}

type SortField = "confidence" | "predicate" | "subject";

/** Produce a stable target ID for an assertion. */
function assertionTargetId(a: Assertion, index: number): string {
  return a.assertion_id ?? `assertion_${a.subject_text}_${a.predicate}_${a.object_text}_${index}`;
}

export default function AssertionPanel({
  assertions,
  onAssertionSelect,
  selectedAssertionId,
  onSeek,
  getStatus,
  onApprove,
  onReject,
  onBulkApprove,
  onBulkReject,
  className = "",
}: AssertionPanelProps) {
  const [sortBy, setSortBy] = useState<SortField>("confidence");
  const [sortAsc, setSortAsc] = useState(false);
  const [filterText, setFilterText] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [groupBy, setGroupBy] = useState<"none" | "frame">("none");
  const [collapsedFrames, setCollapsedFrames] = useState<Set<string>>(new Set());

  // Assertions with stable IDs
  const assertionsWithIds = useMemo(
    () => assertions.map((a, i) => ({ assertion: a, targetId: assertionTargetId(a, i) })),
    [assertions],
  );

  // Apply text filter, status filter, and sort
  const sorted = useMemo(() => {
    let filtered = assertionsWithIds;

    if (filterText) {
      const q = filterText.toLowerCase();
      filtered = filtered.filter(
        ({ assertion: a }) =>
          a.subject_text.toLowerCase().includes(q) ||
          a.predicate.toLowerCase().includes(q) ||
          (a.object_text ?? "").toLowerCase().includes(q),
      );
    }

    if (statusFilter !== "all" && getStatus) {
      filtered = filtered.filter(({ targetId }) => getStatus(targetId) === statusFilter);
    }

    return [...filtered].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "confidence":
          cmp = a.assertion.confidence - b.assertion.confidence;
          break;
        case "predicate":
          // predicate is already canonical (label-pack vocab) on the new
          // contracts-core shape — the legacy split into predicate vs
          // predicate_canonical was retired in commit 6b78435.
          cmp = a.assertion.predicate.localeCompare(b.assertion.predicate);
          break;
        case "subject":
          cmp = a.assertion.subject_text.localeCompare(b.assertion.subject_text);
          break;
      }
      return sortAsc ? cmp : -cmp;
    });
  }, [assertionsWithIds, sortBy, sortAsc, filterText, statusFilter, getStatus]);

  // Visible target IDs for bulk actions
  const visibleTargetIds = useMemo(() => sorted.map((s) => s.targetId), [sorted]);

  // Counts
  const counts = useMemo(() => {
    if (!getStatus) return { approved: 0, rejected: 0, pending: 0, total: visibleTargetIds.length };
    let approved = 0;
    let rejected = 0;
    let pending = 0;
    for (const tid of visibleTargetIds) {
      const s = getStatus(tid);
      if (s === "approved") approved++;
      else if (s === "rejected") rejected++;
      else pending++;
    }
    return { approved, rejected, pending, total: visibleTargetIds.length };
  }, [visibleTargetIds, getStatus]);

  const handleBulkApprove = useCallback(() => {
    if (!getStatus) return;
    const pendingItems = visibleTargetIds
      .filter((tid) => getStatus(tid) === "pending")
      .map((tid) => ({ targetType: "assertion" as const, targetId: tid }));
    onBulkApprove?.(pendingItems);
  }, [visibleTargetIds, getStatus, onBulkApprove]);

  const handleBulkReject = useCallback(() => {
    if (!getStatus) return;
    const pendingItems = visibleTargetIds
      .filter((tid) => getStatus(tid) === "pending")
      .map((tid) => ({ targetType: "assertion" as const, targetId: tid }));
    onBulkReject?.(pendingItems);
  }, [visibleTargetIds, getStatus, onBulkReject]);

  const handleSort = (field: SortField) => {
    if (sortBy === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortBy(field);
      setSortAsc(false);
    }
  };

  const sortIcon = (field: SortField) => {
    if (sortBy !== field) return null;
    return <span className="ml-1 text-[10px]">{sortAsc ? "\u25B2" : "\u25BC"}</span>;
  };

  if (assertions.length === 0) {
    return (
      <div
        className={cn("flex flex-col items-center justify-center gap-2 text-zinc-500", className)}
      >
        <MessageSquareQuote className="h-6 w-6 text-zinc-700" />
        <p className="text-sm">No assertions extracted</p>
      </div>
    );
  }

  return (
    <div data-testid="assertion-panel" className={cn("flex flex-col", className)}>
      {/* Filter */}
      <div className="p-2 border-b border-white/5 flex-shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500" />
          <Input
            data-testid="assertion-search"
            type="text"
            placeholder="Filter assertions..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="pl-8 h-7 text-xs bg-surface-2 border-white/5"
          />
        </div>
      </div>

      {/* Annotation controls */}
      {getStatus && (
        <AnnotationControls
          counts={counts}
          filter={statusFilter}
          onFilterChange={setStatusFilter}
          onApproveAll={handleBulkApprove}
          onRejectAll={handleBulkReject}
        />
      )}

      {/* Sort + group bar */}
      <div className="flex items-center gap-3 px-3 py-1.5 border-b border-white/5 text-[10px] text-zinc-500">
        <button
          className="hover:text-zinc-300 transition-colors"
          onClick={() => handleSort("subject")}
        >
          Subject{sortIcon("subject")}
        </button>
        <button
          className="hover:text-zinc-300 transition-colors"
          onClick={() => handleSort("predicate")}
        >
          Predicate{sortIcon("predicate")}
        </button>
        {/* Group-by toggle — current state acts as the button label */}
        <button
          className={cn(
            "ml-auto inline-flex items-center gap-1 transition-colors",
            groupBy === "frame" ? "text-violet-300" : "text-zinc-500 hover:text-zinc-300",
          )}
          title={groupBy === "frame" ? "Switch to flat view" : "Group by AMR frame (collapsible)"}
          data-testid="assertion-group-toggle"
          onClick={() => setGroupBy(groupBy === "frame" ? "none" : "frame")}
        >
          {groupBy === "frame" ? (
            <>
              <Layers className="h-3 w-3" />
              by frame
            </>
          ) : (
            <>
              <List className="h-3 w-3" />
              flat
            </>
          )}
        </button>
        <button
          className="hover:text-zinc-300 transition-colors"
          onClick={() => handleSort("confidence")}
        >
          Confidence{sortIcon("confidence")}
        </button>
      </div>

      {/* Card list */}
      <ScrollArea className="flex-1">
        {groupBy === "frame" ? (
          <GroupedList
            rows={sorted}
            getStatus={getStatus}
            onApprove={onApprove}
            onReject={onReject}
            onSeek={onSeek}
            onAssertionSelect={onAssertionSelect}
            selectedAssertionId={selectedAssertionId}
            collapsed={collapsedFrames}
            onToggle={(key) =>
              setCollapsedFrames((prev) => {
                const next = new Set(prev);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })
            }
          />
        ) : (
          <div className="divide-y divide-white/[0.03]">
            {sorted.map(({ assertion, targetId }) => {
              const aid =
                assertion.assertion_id ??
                `${assertion.subject_text}_${assertion.predicate}_${assertion.object_text}`;
              return (
                <AssertionCard
                  key={targetId}
                  assertion={assertion}
                  targetId={targetId}
                  status={getStatus ? getStatus(targetId) : "pending"}
                  onApprove={onApprove}
                  onReject={onReject}
                  onSeek={onSeek}
                  onClick={() => {
                    if (onAssertionSelect) {
                      onAssertionSelect(selectedAssertionId === aid ? null : aid);
                    }
                  }}
                  className={
                    selectedAssertionId === aid
                      ? "bg-white/[0.08] ring-1 ring-inset ring-white/10"
                      : ""
                  }
                />
              );
            })}
          </div>
        )}
      </ScrollArea>

      {/* Summary footer */}
      <div className="px-3 py-2 text-[10px] text-zinc-600 border-t border-white/5 flex-shrink-0">
        {sorted.length} of {assertions.length} assertions
      </div>
    </div>
  );
}

// ── GroupedList ─────────────────────────────────────────────────────────────

interface RowWithId {
  assertion: Assertion;
  targetId: string;
}

interface GroupedListProps {
  rows: RowWithId[];
  getStatus?: (targetId: string) => AnnotationStatus;
  onApprove?: (targetId: string) => void;
  onReject?: (targetId: string) => void;
  onSeek?: (timeInSeconds: number) => void;
  onAssertionSelect?: (assertionId: string | null) => void;
  selectedAssertionId?: string | null;
  collapsed: Set<string>;
  onToggle: (key: string) => void;
}

function GroupedList({
  rows,
  getStatus,
  onApprove,
  onReject,
  onSeek,
  onAssertionSelect,
  selectedAssertionId,
  collapsed,
  onToggle,
}: GroupedListProps) {
  // Bucket rows by AMR frame (fall back to predicate when frame is
  // absent — e.g. for structured assertions). Stable insertion order
  // means groups appear in the same order as the sorted list.
  const groups = new Map<string, RowWithId[]>();
  for (const row of rows) {
    const key = row.assertion.amr_frame ?? row.assertion.predicate;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(row);
  }

  return (
    <div className="divide-y divide-white/[0.03]">
      {Array.from(groups.entries()).map(([key, items]) => {
        const isCollapsed = collapsed.has(key);
        const frame = lookupFrame(key);
        return (
          <div key={key}>
            <button
              data-testid={`group-header-${key}`}
              className="w-full sticky top-0 z-10 bg-surface-1 border-b border-white/5 px-3 py-1.5 flex items-center gap-2 text-left hover:bg-white/[0.02] transition-colors"
              onClick={() => onToggle(key)}
            >
              <span className="text-[9px] text-zinc-600 font-mono w-3">
                {isCollapsed ? "▶" : "▼"}
              </span>
              <span className="text-[11px] font-mono text-violet-300">{key}</span>
              <span className="text-[10px] text-zinc-500 tabular-nums">×{items.length}</span>
              {frame && (
                <span className="text-[10px] text-zinc-500 italic truncate">{frame.gloss}</span>
              )}
            </button>
            {!isCollapsed && (
              <div className="divide-y divide-white/[0.03]">
                {items.map(({ assertion, targetId }) => {
                  const aid =
                    assertion.assertion_id ??
                    `${assertion.subject_text}_${assertion.predicate}_${assertion.object_text}`;
                  return (
                    <AssertionCard
                      key={targetId}
                      assertion={assertion}
                      targetId={targetId}
                      status={getStatus ? getStatus(targetId) : "pending"}
                      onApprove={onApprove}
                      onReject={onReject}
                      onSeek={onSeek}
                      onClick={() => {
                        if (onAssertionSelect) {
                          onAssertionSelect(selectedAssertionId === aid ? null : aid);
                        }
                      }}
                      className={
                        selectedAssertionId === aid
                          ? "bg-white/[0.08] ring-1 ring-inset ring-white/10"
                          : ""
                      }
                    />
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
