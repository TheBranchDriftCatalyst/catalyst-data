import { Badge } from "@thebranchdriftcatalyst/catalyst-ui";
import { Check, X, ArrowRight } from "lucide-react";
import type { Assertion, AnnotationStatus } from "@/types/media";
import ConfidenceBadge from "./ConfidenceBadge";
import { cn } from "@/lib/utils";

interface AssertionCardProps {
  assertion: Assertion;
  /** Unique ID used as target_id for annotations. */
  targetId: string;
  status: AnnotationStatus;
  onApprove?: (targetId: string) => void;
  onReject?: (targetId: string) => void;
  onClick?: (assertion: Assertion) => void;
  className?: string;
}

export default function AssertionCard({
  assertion,
  targetId,
  status,
  onApprove,
  onReject,
  onClick,
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
        "group px-3 py-2 hover:bg-white/[0.04] transition-colors cursor-pointer",
        statusBorder,
        className,
      )}
      onClick={() => onClick?.(assertion)}
    >
      {/* SPO triple */}
      <div className="flex items-center gap-1 min-w-0 flex-wrap">
        <span
          className={cn(
            "text-xs font-medium truncate",
            status === "rejected" ? "line-through text-zinc-500" : "text-zinc-200",
          )}
        >
          {assertion.subject_text}
        </span>
        <ArrowRight className="h-3 w-3 text-zinc-600 flex-shrink-0" />
        <span className="text-xs text-zinc-400 truncate">{assertion.predicate}</span>
        <ArrowRight className="h-3 w-3 text-zinc-600 flex-shrink-0" />
        <span
          className={cn(
            "text-xs truncate",
            status === "rejected" ? "line-through text-zinc-500" : "text-zinc-300",
          )}
        >
          {assertion.object_text}
        </span>
      </div>

      {/* Meta row: confidence + flags */}
      <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
        <ConfidenceBadge confidence={assertion.confidence} />

        {assertion.predicate !== assertion.predicate_canonical && (
          <Badge variant="outline" className="text-[9px] px-1 py-0 h-4 text-zinc-500">
            {assertion.predicate_canonical}
          </Badge>
        )}

        {assertion.negated && (
          <Badge variant="destructive" className="text-[9px] px-1 py-0 h-4">
            NEG
          </Badge>
        )}

        {assertion.hedged && (
          <Badge
            variant="outline"
            className="text-[9px] px-1 py-0 h-4 text-yellow-300 border-yellow-800/50"
          >
            HEDGED
          </Badge>
        )}

        {/* Provenance info */}
        {assertion.provenance?.extraction_method && (
          <span className="text-[9px] text-zinc-600 ml-auto">
            {assertion.provenance.extraction_method}
          </span>
        )}
      </div>

      {/* HITL controls */}
      <div
        className={cn(
          "flex items-center gap-1 mt-1.5 transition-opacity",
          status === "pending" ? "opacity-0 group-hover:opacity-100" : "opacity-100",
        )}
      >
        <button
          data-testid="assertion-approve"
          className={cn(
            "p-0.5 rounded transition-colors",
            status === "approved"
              ? "text-green-400 bg-green-900/30"
              : "text-zinc-500 hover:text-green-400 hover:bg-green-900/20",
          )}
          onClick={(e) => {
            e.stopPropagation();
            onApprove?.(targetId);
          }}
          title="Approve"
        >
          <Check className="h-3.5 w-3.5" />
        </button>

        <button
          data-testid="assertion-reject"
          className={cn(
            "p-0.5 rounded transition-colors",
            status === "rejected"
              ? "text-red-400 bg-red-900/30"
              : "text-zinc-500 hover:text-red-400 hover:bg-red-900/20",
          )}
          onClick={(e) => {
            e.stopPropagation();
            onReject?.(targetId);
          }}
          title="Reject"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
