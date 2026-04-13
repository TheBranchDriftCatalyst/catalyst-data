import { Button, Badge } from "@thebranchdriftcatalyst/catalyst-ui";
import { CheckCheck, XCircle } from "lucide-react";
import type { AnnotationStatus } from "@/types/media";
import { cn } from "@/lib/utils";

export type StatusFilter = "all" | AnnotationStatus;

interface AnnotationControlsProps {
  counts: { approved: number; rejected: number; pending: number; total: number };
  filter: StatusFilter;
  onFilterChange: (filter: StatusFilter) => void;
  onApproveAll?: () => void;
  onRejectAll?: () => void;
  disabled?: boolean;
  className?: string;
}

const FILTER_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
];

export default function AnnotationControls({
  counts,
  filter,
  onFilterChange,
  onApproveAll,
  onRejectAll,
  disabled,
  className,
}: AnnotationControlsProps) {
  return (
    <div className={cn("flex items-center gap-1.5 px-2 py-1.5 border-b border-white/5", className)}>
      {/* Filter pills */}
      <div className="flex items-center gap-0.5 flex-1 min-w-0 overflow-x-auto">
        {FILTER_OPTIONS.map((opt) => {
          const count =
            opt.value === "all" ? counts.total : (counts[opt.value as keyof typeof counts] ?? 0);
          const isActive = filter === opt.value;

          return (
            <button
              key={opt.value}
              data-testid={`annotation-filter-${opt.value}`}
              className={cn(
                "flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] transition-colors whitespace-nowrap",
                isActive
                  ? "bg-white/10 text-zinc-200"
                  : "text-zinc-500 hover:text-zinc-300 hover:bg-white/[0.04]",
              )}
              onClick={() => onFilterChange(opt.value)}
            >
              {opt.label}
              <Badge
                variant="secondary"
                className={cn(
                  "text-[9px] px-1 py-0 h-3.5 tabular-nums",
                  isActive ? "bg-white/10" : "bg-transparent",
                )}
              >
                {count}
              </Badge>
            </button>
          );
        })}
      </div>

      {/* Bulk actions */}
      <div className="flex items-center gap-0.5 flex-shrink-0">
        <Button
          data-testid="bulk-approve"
          variant="ghost"
          size="icon-sm"
          className="h-6 w-6 text-zinc-500 hover:text-green-400"
          onClick={onApproveAll}
          disabled={disabled || counts.pending === 0}
          title="Approve all visible"
        >
          <CheckCheck className="h-3.5 w-3.5" />
        </Button>
        <Button
          data-testid="bulk-reject"
          variant="ghost"
          size="icon-sm"
          className="h-6 w-6 text-zinc-500 hover:text-red-400"
          onClick={onRejectAll}
          disabled={disabled || counts.pending === 0}
          title="Reject all visible"
        >
          <XCircle className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
