import { Badge } from "@thebranchdriftcatalyst/catalyst-ui";
import {
  Check,
  X,
  ArrowRight,
  Clock,
  Network,
  Calendar,
  Sparkles,
  Users,
  Tag,
  User as UserIcon,
  CalendarRange,
} from "lucide-react";
import type { Assertion, AnnotationStatus } from "@/types/media";
import type { ExtractionMethod } from "@/types/contracts";
import { hasTemporalWindow } from "@/types/contracts";
import ConfidenceBadge from "./ConfidenceBadge";
import { cn } from "@/lib/utils";
import { formatTime } from "@/lib/speakers";

/** Icon + tooltip label for each extraction method.
 *  Mapped to lucide icons so the row can glyph the source in one
 *  character of horizontal space. */
const EXTRACTION_GLYPHS: Record<
  ExtractionMethod,
  { Icon: typeof Network; label: string; tone: string }
> = {
  amr_projection: { Icon: Network, label: "AMR projection", tone: "text-violet-400" },
  structured: { Icon: Calendar, label: "Structured field", tone: "text-emerald-400" },
  llm: { Icon: Sparkles, label: "LLM extraction", tone: "text-sky-400" },
  ner_ensemble: { Icon: Users, label: "NER ensemble", tone: "text-amber-400" },
  spacy: { Icon: Tag, label: "spaCy", tone: "text-zinc-400" },
  regex: { Icon: Tag, label: "Regex match", tone: "text-zinc-500" },
  manual: { Icon: UserIcon, label: "Manual annotation", tone: "text-pink-400" },
};

/** Render-time formatter for the temporal-validity window. Returns
 *  "2018–2022", "since 2018", "until 2022", or null. */
function formatValidity(from: string | null, until: string | null): string | null {
  const fy = from ? new Date(from).getUTCFullYear() : null;
  const uy = until ? new Date(until).getUTCFullYear() : null;
  if (fy && uy) return `${fy}–${uy}`;
  if (fy) return `since ${fy}`;
  if (uy) return `until ${uy}`;
  return null;
}

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

  const glyph = EXTRACTION_GLYPHS[assertion.provenance.extraction_method];
  // Show the AMR frame separately only when it tells us something the
  // predicate column doesn't — novel frames + frames that differ from
  // the surface predicate (rare but happens via role_overrides).
  const showFrameBadge =
    !!assertion.amr_frame &&
    (assertion.is_novel_predicate || assertion.amr_frame !== assertion.predicate);
  const validityLabel = hasTemporalWindow(assertion)
    ? formatValidity(assertion.t_valid_from, assertion.t_valid_until)
    : null;

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
          {/* object_text is nullable now — intransitive predicates like
              pass-03 with only ARG1 have no object. Render the AMR frame
              as a hint in that case so the row still says something. */}
          {assertion.object_text ?? <em className="text-zinc-500">{assertion.amr_frame ?? "—"}</em>}
        </span>

        {/* Spacer */}
        <span className="flex-1" />

        {/* AMR frame badge — only when distinct from predicate or novel */}
        {showFrameBadge && (
          <Badge
            variant="outline"
            className={cn(
              "text-[8px] px-1 py-0 h-3.5 font-mono flex-shrink-0",
              assertion.is_novel_predicate
                ? "text-orange-300 border-orange-800/50"
                : "text-violet-300 border-violet-800/50",
            )}
            title={
              assertion.is_novel_predicate
                ? `Novel predicate — '${assertion.amr_frame}' not in active label pack`
                : `AMR frame: ${assertion.amr_frame}`
            }
          >
            {assertion.is_novel_predicate ? "NOVEL " : ""}
            {assertion.amr_frame}
          </Badge>
        )}

        {/* Modality (AMR :mode) — rare but informative */}
        {assertion.modality && (
          <Badge
            variant="outline"
            className="text-[8px] px-1 py-0 h-3.5 text-cyan-300 border-cyan-800/50 flex-shrink-0"
            title={`AMR modality: ${assertion.modality}`}
          >
            {assertion.modality}
          </Badge>
        )}

        {/* Temporal validity window (years only — full ISO on hover) */}
        {validityLabel && (
          <span
            className="hidden md:inline-flex items-center gap-0.5 text-[9px] text-emerald-400/80 font-mono tabular-nums flex-shrink-0"
            title={`Valid from ${assertion.t_valid_from ?? "?"} to ${assertion.t_valid_until ?? "open"}`}
          >
            <CalendarRange className="h-2 w-2" />
            {validityLabel}
          </span>
        )}

        {/* Extraction source glyph */}
        {glyph && (
          <span
            className={cn("flex-shrink-0", glyph.tone)}
            title={
              glyph.label +
              (assertion.provenance.extraction_model
                ? ` — ${assertion.provenance.extraction_model}`
                : "")
            }
          >
            <glyph.Icon className="h-2.5 w-2.5" aria-label={glyph.label} />
          </span>
        )}

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
