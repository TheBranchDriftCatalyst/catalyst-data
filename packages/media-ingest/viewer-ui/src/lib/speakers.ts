/**
 * Fixed speaker color palette — 8 distinct contrasting colors.
 * Uses CSS custom properties defined in index.css for consistency.
 */
export const SPEAKER_COLORS = [
  "var(--color-speaker-0)", // blue
  "var(--color-speaker-1)", // red
  "var(--color-speaker-2)", // green
  "var(--color-speaker-3)", // amber
  "var(--color-speaker-4)", // purple
  "var(--color-speaker-5)", // cyan
  "var(--color-speaker-6)", // orange
  "var(--color-speaker-7)", // pink
] as const;

/** Tailwind bg class names for speaker colors */
export const SPEAKER_BG_CLASSES = [
  "bg-[#3b82f6]",
  "bg-[#ef4444]",
  "bg-[#22c55e]",
  "bg-[#f59e0b]",
  "bg-[#a855f7]",
  "bg-[#06b6d4]",
  "bg-[#f97316]",
  "bg-[#ec4899]",
] as const;

/** Tailwind text class names for speaker colors */
export const SPEAKER_TEXT_CLASSES = [
  "text-[#3b82f6]",
  "text-[#ef4444]",
  "text-[#22c55e]",
  "text-[#f59e0b]",
  "text-[#a855f7]",
  "text-[#06b6d4]",
  "text-[#f97316]",
  "text-[#ec4899]",
] as const;

/** Tailwind border-left class names for speaker colors */
export const SPEAKER_BORDER_CLASSES = [
  "border-l-[#3b82f6]",
  "border-l-[#ef4444]",
  "border-l-[#22c55e]",
  "border-l-[#f59e0b]",
  "border-l-[#a855f7]",
  "border-l-[#06b6d4]",
  "border-l-[#f97316]",
  "border-l-[#ec4899]",
] as const;

/**
 * Get the speaker index from a speaker label like "SPEAKER_00".
 * Returns the numeric suffix mod 8 for color mapping.
 */
export function speakerIndex(speaker: string | undefined): number {
  if (!speaker) return 0;
  const match = speaker.match(/(\d+)$/);
  if (!match) return 0;
  return parseInt(match[1]!, 10) % 8;
}

/**
 * Get the CSS color value for a speaker.
 */
export function speakerColor(speaker: string | undefined): string {
  return SPEAKER_COLORS[speakerIndex(speaker)]!;
}

/**
 * Get the Tailwind bg class for a speaker.
 */
export function speakerBgClass(speaker: string | undefined): string {
  return SPEAKER_BG_CLASSES[speakerIndex(speaker)]!;
}

/**
 * Get the Tailwind text class for a speaker.
 */
export function speakerTextClass(speaker: string | undefined): string {
  return SPEAKER_TEXT_CLASSES[speakerIndex(speaker)]!;
}

/**
 * Get the Tailwind border-left class for a speaker.
 */
export function speakerBorderClass(speaker: string | undefined): string {
  return SPEAKER_BORDER_CLASSES[speakerIndex(speaker)]!;
}

/**
 * Format seconds as MM:SS or HH:MM:SS
 */
export function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}
