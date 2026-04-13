import { cn } from "@/lib/utils";

interface ConfidenceBadgeProps {
  confidence: number;
  className?: string;
}

function getConfidenceStyle(confidence: number): { text: string; bg: string; border: string } {
  if (confidence >= 0.8) {
    return {
      text: "text-green-300",
      bg: "bg-green-950/50",
      border: "border-green-800/40",
    };
  }
  if (confidence >= 0.5) {
    return {
      text: "text-yellow-300",
      bg: "bg-yellow-950/50",
      border: "border-yellow-800/40",
    };
  }
  return {
    text: "text-red-300",
    bg: "bg-red-950/50",
    border: "border-red-800/40",
  };
}

export default function ConfidenceBadge({ confidence, className }: ConfidenceBadgeProps) {
  const style = getConfidenceStyle(confidence);
  const pct = (confidence * 100).toFixed(0);

  return (
    <span
      className={cn(
        "inline-flex items-center px-1.5 py-0 h-4 rounded-full text-[10px] font-mono tabular-nums border",
        style.text,
        style.bg,
        style.border,
        className,
      )}
    >
      {pct}%
    </span>
  );
}
