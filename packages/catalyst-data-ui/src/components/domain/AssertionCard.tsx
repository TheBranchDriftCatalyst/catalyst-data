import { Badge } from "@thebranchdriftcatalyst/catalyst-ui";
import { Check, X, ArrowRight, Clock } from "lucide-react";
import type { Assertion, AnnotationStatus } from "@/types/media";
import ConfidenceBadge from "./ConfidenceBadge";
import { cn } from "@/lib/utils";
import { formatTime } from "@/lib/speakers";

interface AssertionCardProps {
  assertion: Assertion;
  /** Unique ID used as target_id for annotations. */
  targetId: string;
  status: AnnotationStatus;
  onApprove?: (targetId: string) => void;
  onReject?: (targetId: string) => void;
  onClick?: (assertion: Assertion) => void;
  /** Called to seek the video to this assertion's timestamp (seconds). */
  onSeek?: (timeInSeconds: number) => void;
  className?: string;
}

export default function AssertionCard({
  assertion,
  targetId,
  status,
  onApprove,
  onReject,
  onClick,
  onSeek,
  className,
}: AssertionCardProps) {
  const statusBorder =
    status === "approved"
      ? "border-l-2 border-l-green-500/60"
      : status === "rejected"
        ? "border-l-2 border-l-red-500/60"
        : "border-l-2 border-l-transparent";

  return (
    <div
      data-testid={`assertion-card-${targetId}`}
      className={cn(
        "group px-3 py-1 hover:bg-white/[0.04] transition-colors cursor-pointer",
        statusBorder,
        className,
      )}
      onClick={() => {
        onClick?.(assertion);
        if (onSeek && assertion.provenance?.temporal_start_ms != null) {
          onSeek(assertion.provenance.temporal_start_ms / 1000);
        }
      }}
    >
      {/* Single row: SPO + metadata + controls */}
      <div className="flex items-center gap-1 min-w-0">
        {/* SPO triple */}
        <span
          className={cn(
            "text-[11px] font-medium truncate",
            status === "rejected" ? "line-through text-zinc-500" : "text-zinc-200",
          )}
        >
          {assertion.subject_text}
        </span>
        <ArrowRight className="h-2.5 w-2.5 text-zinc-600 flex-shrink-0" />
        <span className="text-[11px] text-zinc-400 truncate">{assertion.predicate}</span>
        <ArrowRight className="h-2.5 w-2.5 text-zinc-600 flex-shrink-0" />
        <span
          className={cn(
            "text-[11px] truncate",
            status === "rejected" ? "line-through text-zinc-500" : "text-zinc-300",
          )}
        >
          {assertion.object_text}
        </span>

        {/* Spacer */}
        <span className="flex-1" />

        {/* Timestamp */}
        {assertion.provenance?.temporal_start_ms != null && (
          <span className="hidden sm:inline-flex items-center gap-0.5 text-[9px] text-zinc-600 font-mono tabular-nums flex-shrink-0">
            <Clock className="h-2 w-2" />
            {formatTime(assertion.provenance.temporal_start_ms / 1000)}
          </span>
        )}

        {/* Speaker */}
        {assertion.provenance?.speaker_label && (
          <span className="hidden md:inline text-[9px] text-zinc-600 flex-shrink-0">
            {assertion.provenance.speaker_label}
          </span>
        )}

        {/* Confidence */}
        <ConfidenceBadge confidence={assertion.confidence} className="flex-shrink-0" />

        {/* Flags */}
        {assertion.negated && (
          <Badge variant="destructive" className="text-[8px] px-0.5 py-0 h-3.5">
            NEG
          </Badge>
        )}
        {assertion.hedged && (
          <Badge
            variant="outline"
            className="text-[8px] px-0.5 py-0 h-3.5 text-yellow-300 border-yellow-800/50"
          >
            H
          </Badge>
        )}

        {/* HITL controls — inline */}
        <div
          className={cn(
            "flex items-center gap-0.5 flex-shrink-0 transition-opacity",
            status === "pending" ? "opacity-0 group-hover:opacity-100" : "opacity-100",
          )}
        >
          <button
            data-testid="assertion-approve"
            className={cn(
              "p-0.5 rounded transition-colors",
              status === "approved"
                ? "text-green-400 bg-green-900/30"
                : "text-zinc-600 hover:text-green-400",
            )}
            onClick={(e) => {
              e.stopPropagation();
              onApprove?.(targetId);
            }}
            title="Approve"
          >
            <Check className="h-3 w-3" />
          </button>
          <button
            data-testid="assertion-reject"
            className={cn(
              "p-0.5 rounded transition-colors",
              status === "rejected"
                ? "text-red-400 bg-red-900/30"
                : "text-zinc-600 hover:text-red-400",
            )}
            onClick={(e) => {
              e.stopPropagation();
              onReject?.(targetId);
            }}
            title="Reject"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      </div>
    </div>
  );
}
