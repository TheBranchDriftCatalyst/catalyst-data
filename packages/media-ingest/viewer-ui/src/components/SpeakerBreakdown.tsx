import type { Segment } from "@/types/media";
import { speakerIndex, formatTime } from "@/lib/speakers";

interface SpeakerBreakdownProps {
  segments: Segment[];
  speakers: string[];
  duration: number;
  className?: string;
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

function computeSpeakerStats(
  segments: Segment[],
  speakers: string[],
  _duration: number
): SpeakerStats[] {
  const timeMap = new Map<string, { total: number; count: number }>();

  for (const seg of segments) {
    const key = seg.speaker ?? "Unknown";
    const entry = timeMap.get(key) ?? { total: 0, count: 0 };
    entry.total += seg.end - seg.start;
    entry.count += 1;
    timeMap.set(key, entry);
  }

  const totalSpeaking = Array.from(timeMap.values()).reduce(
    (sum, v) => sum + v.total,
    0
  );
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
  duration,
  className = "",
}: SpeakerBreakdownProps) {
  const stats = computeSpeakerStats(segments, speakers, duration);

  if (stats.length === 0) {
    return (
      <div className={`text-zinc-500 text-sm ${className}`}>
        No speaker data available
      </div>
    );
  }

  return (
    <div className={className}>
      {/* Stacked bar chart */}
      <div className="flex w-full h-6 rounded-md overflow-hidden bg-surface-2">
        {stats.map(({ speaker, percentage }) => {
          const idx = speakerIndex(speaker);
          return (
            <div
              key={speaker}
              className={`${BG_CLASSES[idx]} h-full transition-all duration-300 relative group`}
              style={{ width: `${Math.max(percentage, 1)}%` }}
              title={`${speaker}: ${percentage.toFixed(1)}%`}
            >
              {/* Show label if wide enough */}
              {percentage > 12 && (
                <span className="absolute inset-0 flex items-center justify-center text-[10px] font-medium text-white/90 truncate px-1">
                  {speaker.replace("SPEAKER_", "S")}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* Per-speaker details */}
      <div className="mt-3 space-y-2">
        {stats.map(({ speaker, totalTime, segmentCount, percentage }) => {
          const idx = speakerIndex(speaker);
          return (
            <div key={speaker} className="flex items-center gap-3 text-sm">
              {/* Color dot */}
              <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${BG_CLASSES[idx]}`} />

              {/* Speaker name */}
              <span className={`${TEXT_CLASSES[idx]} font-medium min-w-[100px]`}>
                {speaker}
              </span>

              {/* Progress bar */}
              <div className="flex-1 h-1.5 bg-surface-2 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${BG_CLASSES[idx]} opacity-70`}
                  style={{ width: `${percentage}%` }}
                />
              </div>

              {/* Stats */}
              <span className="text-zinc-400 text-xs tabular-nums min-w-[110px] text-right">
                {formatTime(totalTime)} ({percentage.toFixed(0)}%)
              </span>
              <span className="text-zinc-600 text-xs tabular-nums min-w-[50px] text-right">
                {segmentCount} seg
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
