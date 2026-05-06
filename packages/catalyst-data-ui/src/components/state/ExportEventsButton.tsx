import { useState } from "react";
import { Download } from "lucide-react";
import { downloadEventsNdjson } from "@/lib/exportEvents";

interface ExportEventsButtonProps {
  runId: string | null;
  docId: string | null;
}

export function ExportEventsButton({ runId, docId }: ExportEventsButtonProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isDisabled = !runId || !docId || isLoading;

  const handleClick = async () => {
    if (!runId || !docId) return;

    setIsLoading(true);
    setError(null);

    try {
      await downloadEventsNdjson(runId, docId);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to export events";
      setError(message);
      // Clear error after 5 seconds
      setTimeout(() => setError(null), 5000);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={handleClick}
        disabled={isDisabled}
        data-testid="export-events-button"
        title={
          !runId || !docId
            ? "Select a run and doc to export events"
            : "Export events as NDJSON (max 100k limit)"
        }
        className="flex items-center gap-1.5 px-2 h-6 rounded text-[10px] font-mono text-zinc-300 bg-white/[0.04] hover:bg-white/[0.08] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        <Download className="h-3 w-3 opacity-60" />
        <span>{isLoading ? "exporting…" : "export"}</span>
      </button>
      {error && (
        <span
          data-testid="export-events-error"
          className="text-[9px] text-amber-300 whitespace-nowrap"
        >
          {error}
        </span>
      )}
    </div>
  );
}
