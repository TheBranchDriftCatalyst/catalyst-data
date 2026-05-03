import type { S3ReadResult } from "@/api/client";

/** Amber banner shown above truncated content (the backend caps text reads
 *  at 100KB and JSONL reads at `max_lines`). */
export function TruncationBanner({ content }: { content: S3ReadResult }) {
  const shown = Array.isArray(content.data) ? content.data.length : "partial";
  return (
    <div className="px-4 py-2 text-xs text-amber-400 border-b border-white/5">
      Showing {shown} of {content.total_lines ?? "many"} lines (truncated)
    </div>
  );
}
