import { Badge } from "@thebranchdriftcatalyst/catalyst-ui";
import {
  ArrowRight,
  ShieldCheck,
  ShieldX,
  ShieldQuestion,
  FileText,
  Sparkles,
  AlertTriangle,
  ChevronRight,
} from "lucide-react";
import type { BillClaim, ClaimOperator } from "@/types/billClaims";
import { operatorClass } from "@/types/billClaims";
import { cn } from "@/lib/utils";

/** Display config per operator. Deontic operators use shield icons +
 *  saturated colours (these are the load-bearing legal claims); structural
 *  operators use muted tones (definitions, scope, cross-refs). */
interface OperatorDisplay {
  icon: typeof ShieldCheck;
  label: string;
  tone: string;
  borderTone: string;
}

const OPERATOR_DISPLAY: Record<ClaimOperator, OperatorDisplay> = {
  // Deontic — strongest visual weight
  requires: {
    icon: ShieldCheck,
    label: "REQUIRES",
    tone: "text-emerald-300",
    borderTone: "border-emerald-800/60",
  },
  prohibits: {
    icon: ShieldX,
    label: "PROHIBITS",
    tone: "text-red-300",
    borderTone: "border-red-800/60",
  },
  permits: {
    icon: ShieldQuestion,
    label: "PERMITS",
    tone: "text-amber-300",
    borderTone: "border-amber-800/60",
  },
  // Structural — muted
  defines: {
    icon: FileText,
    label: "DEFINES",
    tone: "text-sky-300",
    borderTone: "border-sky-800/50",
  },
  establishes: {
    icon: Sparkles,
    label: "ESTABLISHES",
    tone: "text-violet-300",
    borderTone: "border-violet-800/50",
  },
  applies_to: {
    icon: ChevronRight,
    label: "APPLIES TO",
    tone: "text-zinc-300",
    borderTone: "border-zinc-700/50",
  },
  amends: {
    icon: FileText,
    label: "AMENDS",
    tone: "text-indigo-300",
    borderTone: "border-indigo-800/50",
  },
  repeals: {
    icon: FileText,
    label: "REPEALS",
    tone: "text-orange-300",
    borderTone: "border-orange-800/50",
  },
  authorizes: {
    icon: ShieldCheck,
    label: "AUTHORIZES",
    tone: "text-cyan-300",
    borderTone: "border-cyan-800/50",
  },
  appropriates: {
    icon: Sparkles,
    label: "APPROPRIATES",
    tone: "text-yellow-300",
    borderTone: "border-yellow-800/50",
  },
  designates: {
    icon: FileText,
    label: "DESIGNATES",
    tone: "text-pink-300",
    borderTone: "border-pink-800/50",
  },
  exempts: {
    icon: ShieldQuestion,
    label: "EXEMPTS",
    tone: "text-lime-300",
    borderTone: "border-lime-800/50",
  },
};

interface ClaimCardProps {
  claim: BillClaim;
  onClick?: (claim: BillClaim) => void;
  selected?: boolean;
  className?: string;
}

/** One row in the Claims tab. Three-line layout:
 *
 *   [operator chip]   actor                              [conf%] [⚠]
 *                     → action
 *                     condition pills (deadline, scope, …)
 *
 *  Click → DetailsPanel renders the full breakdown. */
export default function ClaimCard({ claim, onClick, selected, className }: ClaimCardProps) {
  const display = OPERATOR_DISPLAY[claim.operator];
  const klass = operatorClass(claim.operator);
  const Icon = display.icon;

  return (
    <div
      data-testid={`claim-card-${claim.claim_id}`}
      onClick={() => onClick?.(claim)}
      className={cn(
        "group px-3 py-2.5 cursor-pointer transition-colors border-l-2",
        selected
          ? "bg-white/[0.06] " + display.borderTone.replace(/\/\d+$/, "")
          : "border-l-transparent hover:bg-white/[0.03]",
        className,
      )}
    >
      {/* Row 1: operator chip + actor + flags */}
      <div className="flex items-center gap-2 min-w-0 mb-1">
        <Badge
          variant="outline"
          className={cn(
            "text-[9px] font-mono font-semibold tracking-wider px-1.5 py-0 h-4 gap-1 flex-shrink-0",
            display.tone,
            display.borderTone,
          )}
          title={
            klass === "deontic"
              ? `Deontic operator — imposes ${claim.operator}`
              : `Structural operator — ${claim.operator}`
          }
        >
          <Icon className="h-2.5 w-2.5" />
          {display.label}
        </Badge>
        <span className="text-xs font-medium text-zinc-100 truncate flex-1 min-w-0">
          {claim.actor}
        </span>
        <span
          className={cn(
            "text-[9px] font-mono tabular-nums flex-shrink-0",
            claim.confidence >= 0.9
              ? "text-emerald-400/80"
              : claim.confidence >= 0.7
                ? "text-zinc-400"
                : "text-amber-400/80",
          )}
          title={`LLM self-rated confidence ${(claim.confidence * 100).toFixed(0)}%`}
        >
          {(claim.confidence * 100).toFixed(0)}%
        </span>
        {claim.review_needed && (
          <span
            className="flex-shrink-0 text-amber-400"
            title={claim.review_reason ?? "LLM flagged for human review"}
          >
            <AlertTriangle className="h-3 w-3" />
          </span>
        )}
      </div>

      {/* Row 2: → action */}
      <div className="flex items-start gap-1.5 pl-1 min-w-0">
        <ArrowRight className="h-3 w-3 text-zinc-600 mt-0.5 flex-shrink-0" />
        <span className="text-xs text-zinc-300 leading-snug">{claim.action}</span>
      </div>

      {/* Row 3: condition pills */}
      {claim.conditions.length > 0 && (
        <div className="flex items-center gap-1.5 mt-1.5 pl-1 flex-wrap">
          {claim.conditions.map((c, i) => (
            <Badge
              key={i}
              variant="outline"
              className="text-[9px] font-mono px-1 py-0 h-3.5 text-zinc-400 border-white/10"
              title={c.text}
            >
              <span className="text-zinc-500 mr-1">{c.type}</span>
              <span className="truncate max-w-[200px]">{c.text}</span>
            </Badge>
          ))}
          {claim.exceptions.length > 0 && (
            <Badge
              variant="outline"
              className="text-[9px] font-mono px-1 py-0 h-3.5 text-orange-300/80 border-orange-900/40"
              title={`Exceptions: ${claim.exceptions.join("; ")}`}
            >
              {claim.exceptions.length} exception{claim.exceptions.length === 1 ? "" : "s"}
            </Badge>
          )}
          {claim.penalty && (
            <Badge
              variant="outline"
              className="text-[9px] font-mono px-1 py-0 h-3.5 text-red-300/80 border-red-900/40"
              title={claim.penalty}
            >
              penalty
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}
