import { useCallback, useRef } from "react";
// Tooltip from catalyst-ui is used at the parent level via TooltipProvider
import type { Segment } from "@/types/media";
import { speakerColor, speakerIndex, formatTime } from "@/lib/speakers";
import { cn } from "@/lib/utils";

interface SpeakerTimelineProps {
  segments: Segment[];
  duration: number;
  currentTime: number;
  speakers: string[];
  onSeek: (time: number) => void;
  className?: string;
}

const SPEAKER_BG_SWATCH = [
  "bg-[#3b82f6]",
  "bg-[#ef4444]",
  "bg-[#22c55e]",
  "bg-[#f59e0b]",
  "bg-[#a855f7]",
  "bg-[#06b6d4]",
  "bg-[#f97316]",
  "bg-[#ec4899]",
] as const;

const SPEAKER_TEXT_SWATCH = [
  "text-[#3b82f6]",
  "text-[#ef4444]",
  "text-[#22c55e]",
  "text-[#f59e0b]",
  "text-[#a855f7]",
  "text-[#06b6d4]",
  "text-[#f97316]",
  "text-[#ec4899]",
] as const;

export default function SpeakerTimeline({
  segments,
  duration,
  currentTime,
  speakers,
  onSeek,
  className = "",
}: SpeakerTimelineProps) {
  const barRef = useRef<HTMLDivElement>(null);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const bar = barRef.current;
      if (!bar || duration <= 0) return;
      const rect = bar.getBoundingClientRect();
      const fraction = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      onSeek(fraction * duration);
    },
    [duration, onSeek],
  );

  const playheadPercent = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div className={cn("select-none", className)}>
      {/* Timeline bar */}
      <div
        ref={barRef}
        className="relative w-full h-10 bg-surface-2 rounded-md cursor-pointer overflow-hidden group"
        onClick={handleClick}
        role="slider"
        aria-label="Speaker timeline"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={currentTime}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") onSeek(Math.min(duration, currentTime + 5));
          if (e.key === "ArrowLeft") onSeek(Math.max(0, currentTime - 5));
        }}
      >
        {/* Segment blocks */}
        {segments.map((seg, i) => {
          const leftPct = (seg.start / duration) * 100;
          const widthPct = ((seg.end - seg.start) / duration) * 100;
          return (
            <div
              key={i}
              className="absolute top-0 h-full opacity-80 hover:opacity-100 transition-opacity"
              style={{
                left: `${leftPct}%`,
                width: `${Math.max(widthPct, 0.2)}%`,
                backgroundColor: speakerColor(seg.speaker),
              }}
              title={`${seg.speaker ?? "Unknown"}: ${formatTime(seg.start)} - ${formatTime(seg.end)}`}
            />
          );
        })}

        {/* Playhead */}
        <div
          className="absolute top-0 h-full w-0.5 bg-white shadow-[0_0_6px_rgba(255,255,255,0.5)] z-10 pointer-events-none transition-[left] duration-75"
          style={{ left: `${playheadPercent}%` }}
        />
      </div>

      {/* Time markers */}
      <div className="flex justify-between mt-1 text-[10px] text-zinc-500 font-mono tabular-nums">
        <span>{formatTime(currentTime)}</span>
        <span>{formatTime(duration)}</span>
      </div>

      {/* Speaker legend */}
      {speakers.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
          {speakers.map((speaker) => {
            const idx = speakerIndex(speaker);
            return (
              <div key={speaker} className="flex items-center gap-1.5 text-xs">
                <div className={cn("w-3 h-3 rounded-sm", SPEAKER_BG_SWATCH[idx])} />
                <span className={SPEAKER_TEXT_SWATCH[idx]}>{speaker}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
