import { useState, useCallback } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@thebranchdriftcatalyst/catalyst-ui";
import { Users, Check, Pencil } from "lucide-react";
import type { Segment } from "@/types/media";
import { speakerIndex, formatTime } from "@/lib/speakers";
import { cn } from "@/lib/utils";

interface SpeakerBreakdownProps {
  segments: Segment[];
  speakers: string[];
  duration: number;
  className?: string;
  /** label -> display_name map (empty if not yet loaded). */
  speakerNames?: Record<string, string>;
  /** Called when the operator saves a display name. */
  onSpeakerNameChange?: (label: string, displayName: string) => void;
}

const BG_CLASSES = [
  "bg-[#3b82f6]",
  "bg-[#ef4444]",
  "bg-[#22c55e]",
  "bg-[#f59e0b]",
  "bg-[#a855f7]",
  "bg-[#06b6d4]",
  "bg-[#f97316]",
  "bg-[#ec4899]",
] as const;

const TEXT_CLASSES = [
  "text-[#3b82f6]",
  "text-[#ef4444]",
  "text-[#22c55e]",
  "text-[#f59e0b]",
  "text-[#a855f7]",
  "text-[#06b6d4]",
  "text-[#f97316]",
  "text-[#ec4899]",
] as const;

interface SpeakerStats {
  speaker: string;
  totalTime: number;
  segmentCount: number;
  percentage: number;
}

function computeSpeakerStats(segments: Segment[], speakers: string[]): SpeakerStats[] {
  const timeMap = new Map<string, { total: number; count: number }>();

  for (const seg of segments) {
    const key = seg.speaker ?? "Unknown";
    const entry = timeMap.get(key) ?? { total: 0, count: 0 };
    entry.total += seg.end - seg.start;
    entry.count += 1;
    timeMap.set(key, entry);
  }

  const totalSpeaking = Array.from(timeMap.values()).reduce((sum, v) => sum + v.total, 0);
  const denominator = totalSpeaking > 0 ? totalSpeaking : 1;

  return speakers
    .map((speaker) => {
      const entry = timeMap.get(speaker) ?? { total: 0, count: 0 };
      return {
        speaker,
        totalTime: entry.total,
        segmentCount: entry.count,
        percentage: (entry.total / denominator) * 100,
      };
    })
    .sort((a, b) => b.totalTime - a.totalTime);
}

export default function SpeakerBreakdown({
  segments,
  speakers,
  duration: _duration,
  className = "",
  speakerNames = {},
  onSpeakerNameChange,
}: SpeakerBreakdownProps) {
  const stats = computeSpeakerStats(segments, speakers);

  if (stats.length === 0) {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center gap-2 text-zinc-500 py-8",
          className,
        )}
      >
        <Users className="h-6 w-6 text-zinc-700" />
        <p className="text-sm">No speaker data available</p>
      </div>
    );
  }

  return (
    <div data-testid="speaker-breakdown" className={className}>
      {/* Stacked bar chart */}
      <div className="flex w-full h-7 rounded-md overflow-hidden bg-surface-2">
        {stats.map(({ speaker, percentage }) => {
          const idx = speakerIndex(speaker);
          const displayName = speakerNames[speaker];
          const barLabel = displayName || speaker.replace("SPEAKER_", "S");
          return (
            <Tooltip key={speaker}>
              <TooltipTrigger asChild>
                <div
                  className={cn(
                    BG_CLASSES[idx],
                    "h-full transition-all duration-300 relative group cursor-default",
                  )}
                  style={{ width: `${Math.max(percentage, 1)}%` }}
                >
                  {/* Show label if wide enough */}
                  {percentage > 12 && (
                    <span className="absolute inset-0 flex items-center justify-center text-[10px] font-medium text-white/90 truncate px-1">
                      {barLabel}
                    </span>
                  )}
                </div>
              </TooltipTrigger>
              <TooltipContent>
                {displayName ? `${displayName} (${speaker})` : speaker}: {percentage.toFixed(1)}%
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>

      {/* Per-speaker details */}
      <div className="mt-4 space-y-3">
        {stats.map(({ speaker, totalTime, segmentCount, percentage }) => {
          const idx = speakerIndex(speaker);
          return (
            <div key={speaker} data-testid={`speaker-row-${speaker}`} className="space-y-1">
              <div className="flex items-center gap-3 text-sm">
                {/* Color dot */}
                <div className={cn("w-2.5 h-2.5 rounded-full flex-shrink-0", BG_CLASSES[idx])} />

                {/* Speaker label */}
                <span className={cn(TEXT_CLASSES[idx], "font-medium min-w-[100px] text-xs")}>
                  {speaker}
                </span>

                {/* Progress bar */}
                <div className="flex-1 h-1.5 bg-surface-2 rounded-full overflow-hidden">
                  <div
                    className={cn("h-full rounded-full opacity-70", BG_CLASSES[idx])}
                    style={{ width: `${percentage}%` }}
                  />
                </div>

                {/* Stats */}
                <span className="text-zinc-400 text-xs tabular-nums min-w-[100px] text-right font-mono">
                  {formatTime(totalTime)} ({percentage.toFixed(0)}%)
                </span>
                <span className="text-zinc-600 text-xs tabular-nums min-w-[45px] text-right">
                  {segmentCount} seg
                </span>
              </div>

              {/* Editable display name */}
              {onSpeakerNameChange && (
                <SpeakerNameInput
                  speaker={speaker}
                  currentName={speakerNames[speaker] ?? ""}
                  onSave={onSpeakerNameChange}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Inline editable text field for a speaker display name. */
function SpeakerNameInput({
  speaker,
  currentName,
  onSave,
}: {
  speaker: string;
  currentName: string;
  onSave: (label: string, displayName: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(currentName);

  // Sync external changes when not editing
  if (!editing && value !== currentName) {
    setValue(currentName);
  }

  const handleSave = useCallback(() => {
    const trimmed = value.trim();
    setEditing(false);
    if (trimmed !== currentName) {
      onSave(speaker, trimmed);
    }
  }, [value, currentName, onSave, speaker]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        handleSave();
      } else if (e.key === "Escape") {
        setValue(currentName);
        setEditing(false);
      }
    },
    [handleSave, currentName],
  );

  if (!editing) {
    return (
      <button
        className="ml-8 flex items-center gap-1.5 text-[11px] text-zinc-500 hover:text-zinc-300 transition-colors group"
        onClick={() => setEditing(true)}
      >
        <Pencil className="h-2.5 w-2.5 opacity-0 group-hover:opacity-100 transition-opacity" />
        {currentName ? (
          <span className="text-zinc-400">{currentName}</span>
        ) : (
          <span className="italic text-zinc-600">Set display name...</span>
        )}
      </button>
    );
  }

  return (
    <div className="ml-8 flex items-center gap-1.5">
      <input
        type="text"
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={handleSave}
        onKeyDown={handleKeyDown}
        placeholder="Display name"
        className="h-6 px-1.5 text-[11px] bg-surface-2 border border-white/10 rounded text-zinc-200 placeholder:text-zinc-600 outline-none focus:border-blue-500/50 w-40"
      />
      <button
        className="h-5 w-5 flex items-center justify-center rounded hover:bg-white/10 text-zinc-400"
        onMouseDown={(e) => {
          e.preventDefault(); // prevent blur
          handleSave();
        }}
        title="Save"
      >
        <Check className="h-3 w-3" />
      </button>
    </div>
  );
}
