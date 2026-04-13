import { useMemo } from "react";
import type { Mention, Assertion, Transcription, TimelineMarker } from "@/types/media";

// ── Entity type colors ───────────────────────────────────────────────
const ENTITY_COLORS: Record<string, string> = {
  PERSON: "#3b82f6",
  ORG: "#a855f7",
  GPE: "#22c55e",
  LOC: "#06b6d4",
  DATE: "#f59e0b",
  MONEY: "#ef4444",
  EVENT: "#f97316",
  LAW: "#ec4899",
  NORP: "#8b5cf6",
  FACILITY: "#14b8a6",
  OTHER: "#6b7280",
};

function entityColor(type: string): string {
  return ENTITY_COLORS[type.toUpperCase()] ?? ENTITY_COLORS.OTHER!;
}

function assertionColor(confidence: number): string {
  if (confidence >= 0.8) return "#22c55e";
  if (confidence >= 0.5) return "#f59e0b";
  return "#ef4444";
}

// ── Chunk-to-timestamp mapping ────────────────────────────────────────

/** Extract chunk index from a chunk_id like "doc-abc_chunk_3" or "doc-abc:chunk-3". */
function parseChunkIndex(chunkId: string): number | null {
  const match = /[_:]chunk[_-](\d+)$/.exec(chunkId);
  if (!match) return null;
  return parseInt(match[1]!, 10);
}

/**
 * Find the transcript segment that contains the given text.
 * Chunks span multiple segments, so we can't use chunk_index as segment_index.
 * Instead, search segments for the mention/assertion text.
 *
 * Uses chunk_index as a rough estimate for search start position, then
 * expands outward. Falls back to full linear scan.
 */
function findSegmentByText(
  text: string,
  segments: Transcription["segments"],
  chunkIndex: number | null,
): { start: number; end: number } | null {
  if (segments.length === 0) return null;

  const needle = text.toLowerCase();

  // Estimate: each chunk ≈ 7-8 segments, so chunk N starts around segment N*8
  const estimate = chunkIndex != null ? Math.min(chunkIndex * 8, segments.length - 1) : 0;

  // Search outward from estimate, up to 30 segments in each direction
  const radius = 30;
  const lo = Math.max(0, estimate - radius);
  const hi = Math.min(segments.length - 1, estimate + radius);

  for (let i = lo; i <= hi; i++) {
    if (segments[i]!.text.toLowerCase().includes(needle)) {
      return { start: segments[i]!.start, end: segments[i]!.end };
    }
  }

  // Full scan fallback (rare — only if estimate was way off)
  for (let i = 0; i < segments.length; i++) {
    if (segments[i]!.text.toLowerCase().includes(needle)) {
      return { start: segments[i]!.start, end: segments[i]!.end };
    }
  }

  return null;
}

/**
 * Get the timestamp for a mention, using provenance temporal data first,
 * then text-based segment search as fallback.
 */
function getMentionTimestamp(
  mention: Mention,
  segments: Transcription["segments"],
): { start: number; end: number } | null {
  // 1. Prefer provenance temporal timestamps (most precise)
  const prov = mention.provenance;
  if (prov?.temporal_start_ms != null) {
    return {
      start: prov.temporal_start_ms / 1000,
      end:
        prov.temporal_end_ms != null ? prov.temporal_end_ms / 1000 : prov.temporal_start_ms / 1000,
    };
  }

  // 2. Text-based search within transcript segments
  const chunkId = prov?.chunk_id ?? mention.chunk_id;
  const chunkIdx = chunkId ? parseChunkIndex(chunkId) : null;
  return findSegmentByText(mention.text, segments, chunkIdx);
}

/**
 * Get the timestamp for an assertion via its provenance.
 */
function getAssertionTimestamp(
  assertion: Assertion,
  segments: Transcription["segments"],
): { start: number; end: number } | null {
  const prov = assertion.provenance;
  if (!prov) return null;

  // 1. Prefer temporal timestamps
  if (prov.temporal_start_ms != null) {
    return {
      start: prov.temporal_start_ms / 1000,
      end:
        prov.temporal_end_ms != null ? prov.temporal_end_ms / 1000 : prov.temporal_start_ms / 1000,
    };
  }

  // 2. Text-based search — try subject_text first (most specific), then object
  const chunkIdx = prov.chunk_id ? parseChunkIndex(prov.chunk_id) : null;
  return (
    findSegmentByText(assertion.subject_text, segments, chunkIdx) ??
    findSegmentByText(assertion.object_text, segments, chunkIdx)
  );
}

// ── Hook ──────────────────────────────────────────────────────────────

interface UseMarkerDataParams {
  mentions: Mention[];
  assertions: Assertion[];
  transcription: Transcription | null;
  selectedEntityText?: string | null;
  selectedAssertionId?: string | null;
}

/**
 * Maps mentions and assertions to timeline markers positioned on the video scrubber.
 *
 * When selectedEntityText is set, only markers for that entity text are returned.
 * When selectedAssertionId is set, only that assertion's marker is returned.
 * When neither is set, all markers are returned.
 */
export function useMarkerData({
  mentions,
  assertions,
  transcription,
  selectedEntityText,
  selectedAssertionId,
}: UseMarkerDataParams): TimelineMarker[] {
  return useMemo(() => {
    const segments = transcription?.segments ?? [];
    const markers: TimelineMarker[] = [];

    // If an assertion is selected, only show that assertion's marker
    if (selectedAssertionId) {
      for (const a of assertions) {
        const aid = a.assertion_id ?? `${a.subject_text}_${a.predicate}_${a.object_text}`;
        if (aid !== selectedAssertionId) continue;

        const ts = getAssertionTimestamp(a, segments);
        if (!ts) continue;

        markers.push({
          id: `assertion-${aid}`,
          timestamp: ts.start,
          endTimestamp: ts.end !== ts.start ? ts.end : undefined,
          label: `${a.subject_text} ${a.predicate} ${a.object_text}`,
          color: assertionColor(a.confidence),
          type: "assertion",
          category: a.predicate_canonical || a.predicate,
        });
      }
      return markers;
    }

    // Entity mentions
    const mentionsToProcess = selectedEntityText
      ? mentions.filter((m) => m.text.trim().toLowerCase() === selectedEntityText.toLowerCase())
      : mentions;

    for (let i = 0; i < mentionsToProcess.length; i++) {
      const m = mentionsToProcess[i]!;
      const ts = getMentionTimestamp(m, segments);
      if (!ts) continue;

      markers.push({
        id: `mention-${m.chunk_id}-${i}`,
        timestamp: ts.start,
        endTimestamp: ts.end !== ts.start ? ts.end : undefined,
        label: `${m.text} (${m.mention_type})`,
        color: entityColor(m.mention_type),
        type: "entity",
        category: m.mention_type,
      });
    }

    // Assertions (only when no entity filter is active)
    if (!selectedEntityText) {
      for (let i = 0; i < assertions.length; i++) {
        const a = assertions[i]!;
        const ts = getAssertionTimestamp(a, segments);
        if (!ts) continue;

        const aid = a.assertion_id ?? `${a.subject_text}_${a.predicate}_${a.object_text}_${i}`;
        markers.push({
          id: `assertion-${aid}`,
          timestamp: ts.start,
          endTimestamp: ts.end !== ts.start ? ts.end : undefined,
          label: `${a.subject_text} ${a.predicate} ${a.object_text}`,
          color: assertionColor(a.confidence),
          type: "assertion",
          category: a.predicate_canonical || a.predicate,
        });
      }
    }

    return markers;
  }, [mentions, assertions, transcription, selectedEntityText, selectedAssertionId]);
}
