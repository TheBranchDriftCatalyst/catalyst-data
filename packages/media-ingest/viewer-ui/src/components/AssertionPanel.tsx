import { useState, useMemo, useCallback } from "react";
import { Input, ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { Search, MessageSquareQuote } from "lucide-react";
import type { Assertion, AnnotationStatus } from "@/types/media";
import { AssertionCard, AnnotationControls, type StatusFilter } from "./domain";
import { cn } from "@/lib/utils";

interface AssertionPanelProps {
  assertions: Assertion[];
  onAssertionSelect?: (assertionId: string | null) => void;
  selectedAssertionId?: string | null;
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
          a.object_text.toLowerCase().includes(q),
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
          cmp = a.assertion.predicate_canonical.localeCompare(b.assertion.predicate_canonical);
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

      {/* Sort bar */}
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
        <button
          className="hover:text-zinc-300 transition-colors ml-auto"
          onClick={() => handleSort("confidence")}
        >
          Confidence{sortIcon("confidence")}
        </button>
      </div>

      {/* Card list */}
      <ScrollArea className="flex-1">
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
      </ScrollArea>

      {/* Summary footer */}
      <div className="px-3 py-2 text-[10px] text-zinc-600 border-t border-white/5 flex-shrink-0">
        {sorted.length} of {assertions.length} assertions
      </div>
    </div>
  );
}
