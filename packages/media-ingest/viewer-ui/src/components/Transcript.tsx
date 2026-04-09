import { useRef, useEffect, useCallback } from "react";
import type { Segment, Word } from "@/types/media";
import { speakerIndex, formatTime } from "@/lib/speakers";

interface TranscriptProps {
  segments: Segment[];
  activeSegmentIndex: number;
  activeWordIndex: number;
  onSeek: (time: number) => void;
  highlightText?: string;
  className?: string;
}

// Pre-built border classes to avoid dynamic generation
const BORDER_CLASSES = [
  "border-l-[#3b82f6]",
  "border-l-[#ef4444]",
  "border-l-[#22c55e]",
  "border-l-[#f59e0b]",
  "border-l-[#a855f7]",
  "border-l-[#06b6d4]",
  "border-l-[#f97316]",
  "border-l-[#ec4899]",
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

export default function Transcript({
  segments,
  activeSegmentIndex,
  activeWordIndex,
  onSeek,
  highlightText,
  className = "",
}: TranscriptProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const activeSegRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to active segment
  useEffect(() => {
    if (activeSegRef.current && containerRef.current) {
      const container = containerRef.current;
      const element = activeSegRef.current;
      const containerRect = container.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();

      // Only scroll if element is out of view
      const isVisible =
        elementRect.top >= containerRect.top &&
        elementRect.bottom <= containerRect.bottom;

      if (!isVisible) {
        element.scrollIntoView({
          behavior: "smooth",
          block: "center",
        });
      }
    }
  }, [activeSegmentIndex]);

  const handleSegmentClick = useCallback(
    (time: number) => {
      onSeek(time);
    },
    [onSeek]
  );

  if (segments.length === 0) {
    return (
      <div className={`flex items-center justify-center text-zinc-500 text-sm ${className}`}>
        No transcript available
      </div>
    );
  }

  // Group consecutive segments by speaker for visual grouping
  let lastSpeaker: string | null = null;

  return (
    <div
      ref={containerRef}
      className={`overflow-y-auto transcript-scroll ${className}`}
    >
      <div className="space-y-1 p-3">
        {segments.map((seg, segIdx) => {
          const isActive = segIdx === activeSegmentIndex;
          const idx = speakerIndex(seg.speaker);
          const borderClass = BORDER_CLASSES[idx]!;
          const textClass = TEXT_CLASSES[idx]!;
          const showSpeakerLabel = seg.speaker !== lastSpeaker;
          lastSpeaker = seg.speaker ?? null;

          return (
            <div key={segIdx}>
              {/* Speaker label when speaker changes */}
              {showSpeakerLabel && seg.speaker && (
                <div className={`text-xs font-semibold mt-3 mb-1 ${textClass}`}>
                  {seg.speaker}
                  <span className="text-zinc-600 font-normal ml-2">
                    {formatTime(seg.start)}
                  </span>
                </div>
              )}

              {/* Segment block */}
              <div
                ref={isActive ? activeSegRef : undefined}
                className={`
                  px-3 py-1.5 rounded-r-md cursor-pointer transition-all duration-200
                  border-l-2 ${borderClass}
                  ${isActive
                    ? "bg-white/[0.08] segment-active"
                    : "hover:bg-white/[0.04]"
                  }
                `}
                onClick={() => handleSegmentClick(seg.start)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleSegmentClick(seg.start);
                  }
                }}
                aria-label={`Segment at ${formatTime(seg.start)}: ${seg.text.slice(0, 50)}`}
              >
                {/* Word-level rendering when available */}
                {seg.words && seg.words.length > 0 ? (
                  <p className="text-sm leading-relaxed">
                    {seg.words.map((word, wIdx) => (
                      <WordSpan
                        key={wIdx}
                        word={word}
                        isActive={isActive && wIdx === activeWordIndex}
                        highlightText={highlightText}
                        onClick={() => onSeek(word.start)}
                      />
                    ))}
                  </p>
                ) : (
                  <p className="text-sm leading-relaxed text-zinc-300">
                    {highlightText
                      ? highlightInText(seg.text, highlightText)
                      : seg.text}
                  </p>
                )}

                {/* Timestamp (subtle, on hover) */}
                {!showSpeakerLabel && (
                  <span className="text-[10px] text-zinc-600 opacity-0 group-hover:opacity-100 transition-opacity">
                    {formatTime(seg.start)}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Individual word span with active highlighting (karaoke effect) */
function WordSpan({
  word,
  isActive,
  highlightText,
  onClick,
}: {
  word: Word;
  isActive: boolean;
  highlightText?: string;
  onClick: () => void;
}) {
  const text = word.word;
  const isHighlighted =
    highlightText &&
    text.toLowerCase().includes(highlightText.toLowerCase());

  return (
    <span
      className={`
        cursor-pointer transition-colors duration-100 rounded-sm
        ${isActive ? "bg-accent text-zinc-950 font-medium px-0.5" : ""}
        ${isHighlighted && !isActive ? "bg-amber-900/50 text-amber-200" : ""}
        ${!isActive && !isHighlighted ? "text-zinc-300 hover:text-zinc-100" : ""}
      `}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={`${formatTime(word.start)} (${(word.probability * 100).toFixed(0)}% conf)`}
    >
      {text}
    </span>
  );
}

/** Highlight matching text within a string */
function highlightInText(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const regex = new RegExp(`(${escapeRegex(query)})`, "gi");
  const parts = text.split(regex);
  return parts.map((part, i) =>
    regex.test(part) ? (
      <mark key={i} className="bg-amber-900/50 text-amber-200 rounded-sm px-0.5">
        {part}
      </mark>
    ) : (
      part
    )
  );
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
