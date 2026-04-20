import { useState, useCallback } from "react";
import { Badge, Input } from "@thebranchdriftcatalyst/catalyst-ui";
import { Check, X, Clock } from "lucide-react";
import type { Mention, AnnotationStatus } from "@/types/media";
import { cn } from "@/lib/utils";
import { formatTime } from "@/lib/speakers";

// ── Type config (mirrors EntityPanel for consistency) ────────────────────

const TYPE_COLORS: Record<string, { color: string; bg: string }> = {
  PERSON: { color: "text-blue-300", bg: "bg-blue-900/40" },
  ORG: { color: "text-purple-300", bg: "bg-purple-900/40" },
  GPE: { color: "text-green-300", bg: "bg-green-900/40" },
  LOC: { color: "text-emerald-300", bg: "bg-emerald-900/40" },
  DATE: { color: "text-amber-300", bg: "bg-amber-900/40" },
  EVENT: { color: "text-rose-300", bg: "bg-rose-900/40" },
  PRODUCT: { color: "text-cyan-300", bg: "bg-cyan-900/40" },
  WORK_OF_ART: { color: "text-indigo-300", bg: "bg-indigo-900/40" },
  LAW: { color: "text-red-300", bg: "bg-red-900/40" },
  MONEY: { color: "text-yellow-300", bg: "bg-yellow-900/40" },
  QUANTITY: { color: "text-teal-300", bg: "bg-teal-900/40" },
  NORP: { color: "text-fuchsia-300", bg: "bg-fuchsia-900/40" },
};

const DEFAULT_TYPE_COLOR = { color: "text-zinc-300", bg: "bg-zinc-800" };

function getTypeColor(type: string) {
  return TYPE_COLORS[type.toUpperCase()] ?? DEFAULT_TYPE_COLOR;
}

// ── Component ────────────────────────────────────────────────────────────

interface MentionCardProps {
  mention: Mention;
  /** Unique ID used as target_id for annotations. */
  targetId: string;
  status: AnnotationStatus;
  /** Number of times this entity appears across all chunks. */
  occurrenceCount?: number;
  /** Earliest temporal_start_ms across all mentions. */
  firstSeenMs?: number | null;
  /** Latest temporal_end_ms across all mentions. */
  lastSeenMs?: number | null;
  /** Unique speaker labels who mentioned this entity. */
  speakers?: string[];
  onApprove?: (targetId: string) => void;
  onReject?: (targetId: string) => void;
  onEdit?: (targetId: string, edits: Record<string, unknown>) => void;
  onClick?: (mention: Mention) => void;
  /** Called to seek the video/transcript to this mention's timestamp (seconds). */
  onSeek?: (timeInSeconds: number) => void;
  className?: string;
}

export default function MentionCard({
  mention,
  targetId,
  status,
  occurrenceCount,
  firstSeenMs,
  lastSeenMs,
  speakers,
  onApprove,
  onReject,
  onEdit,
  onClick,
  onSeek,
  className,
}: MentionCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(mention.text);
  const typeStyle = getTypeColor(mention.mention_type);

  const handleSaveEdit = useCallback(() => {
    if (editValue.trim() && editValue !== mention.text) {
      onEdit?.(targetId, { corrected_text: editValue.trim() });
    }
    setIsEditing(false);
  }, [editValue, mention.text, onEdit, targetId]);

  const handleCancelEdit = useCallback(() => {
    setEditValue(mention.text);
    setIsEditing(false);
  }, [mention.text]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") handleSaveEdit();
      if (e.key === "Escape") handleCancelEdit();
    },
    [handleSaveEdit, handleCancelEdit],
  );

  const statusBorder =
    status === "approved"
      ? "border-l-2 border-l-green-500/60"
      : status === "rejected"
        ? "border-l-2 border-l-red-500/60"
        : "border-l-2 border-l-transparent";

  return (
    <div
      data-testid={`mention-card-${targetId}`}
      className={cn(
        "group flex items-center gap-1.5 px-3 py-1 hover:bg-white/[0.04] transition-colors cursor-pointer min-h-[28px]",
        statusBorder,
        className,
      )}
      onClick={() => {
        onClick?.(mention);
        if (onSeek && mention.provenance?.temporal_start_ms != null) {
          onSeek(mention.provenance.temporal_start_ms / 1000);
        }
      }}
    >
      {/* Entity name */}
      {isEditing ? (
        <Input
          type="text"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleSaveEdit}
          autoFocus
          className="h-5 text-xs bg-surface-2 border-white/10 flex-1"
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span
          className={cn(
            "text-xs truncate flex-1 min-w-0",
            status === "rejected" ? "line-through text-zinc-500" : "text-zinc-200",
          )}
        >
          {mention.text}
        </span>
      )}

      {/* Temporal range */}
      {firstSeenMs != null && lastSeenMs != null ? (
        <span className="hidden sm:inline-flex items-center gap-0.5 text-[9px] text-zinc-600 font-mono tabular-nums flex-shrink-0">
          <Clock className="h-2 w-2" />
          {formatTime(firstSeenMs / 1000)}–{formatTime(lastSeenMs / 1000)}
        </span>
      ) : mention.provenance?.temporal_start_ms != null ? (
        <span className="hidden sm:inline-flex items-center gap-0.5 text-[9px] text-zinc-600 font-mono tabular-nums flex-shrink-0">
          <Clock className="h-2 w-2" />
          {formatTime(mention.provenance.temporal_start_ms / 1000)}
        </span>
      ) : null}

      {/* Speakers */}
      {speakers && speakers.length > 0 && (
        <span className="hidden md:inline text-[9px] text-zinc-600 flex-shrink-0">
          {speakers.join(", ")}
        </span>
      )}

      {/* Type badge */}
      <Badge
        variant="secondary"
        className={cn(
          "text-[8px] px-1 py-0 h-3.5 uppercase flex-shrink-0",
          typeStyle.color,
          typeStyle.bg,
        )}
      >
        {mention.mention_type}
      </Badge>

      {/* Count badge */}
      {occurrenceCount != null && occurrenceCount > 1 && (
        <span className="text-[9px] text-zinc-500 tabular-nums flex-shrink-0">
          ×{occurrenceCount}
        </span>
      )}

      {/* HITL controls — inline, visible on hover */}
      <div
        className={cn(
          "flex items-center gap-0.5 flex-shrink-0 transition-opacity",
          status === "pending" ? "opacity-0 group-hover:opacity-100" : "opacity-100",
        )}
      >
        <button
          data-testid="mention-approve"
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
          data-testid="mention-reject"
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
  );
}
