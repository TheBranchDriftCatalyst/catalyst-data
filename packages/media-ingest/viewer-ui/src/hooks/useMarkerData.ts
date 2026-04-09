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

/**
 * Build a lookup from chunk index to the transcription segment's start/end time.
 *
 * chunk_id format: `{document_id}_chunk_{index}`
 * We map chunk index -> segment index (1:1 when chunks come from transcription segments).
 */
function buildChunkTimestampMap(
  transcription: Transcription | null,
): Map<number, { start: number; end: number }> {
  const map = new Map<number, { start: number; end: number }>();
  if (!transcription) return map;

  for (let i = 0; i < transcription.segments.length; i++) {
    const seg = transcription.segments[i]!;
    map.set(i, { start: seg.start, end: seg.end });
  }
  return map;
}

/** Extract chunk index from a chunk_id like "doc-abc_chunk_3". */
function parseChunkIndex(chunkId: string): number | null {
  const match = /_chunk_(\d+)$/.exec(chunkId);
  if (!match) return null;
  return parseInt(match[1]!, 10);
}

/**
 * Get the timestamp for a mention, using provenance temporal data first,
 * then falling back to chunk_id mapping into transcription segments.
 */
function getMentionTimestamp(
  mention: Mention,
  chunkMap: Map<number, { start: number; end: number }>,
): { start: number; end: number } | null {
  // 1. Prefer provenance temporal timestamps (most precise)
  const prov = mention.provenance;
  if (prov?.temporal_start_ms != null) {
    return {
      start: prov.temporal_start_ms / 1000,
      end: prov.temporal_end_ms != null ? prov.temporal_end_ms / 1000 : prov.temporal_start_ms / 1000,
    };
  }

  // 2. Fall back to chunk_id -> segment mapping
  const chunkId = prov?.chunk_id ?? mention.chunk_id;
  if (!chunkId) return null;

  const idx = parseChunkIndex(chunkId);
  if (idx === null) return null;

  return chunkMap.get(idx) ?? null;
}

/**
 * Get the timestamp for an assertion via its provenance.
 */
function getAssertionTimestamp(
  assertion: Assertion,
  chunkMap: Map<number, { start: number; end: number }>,
): { start: number; end: number } | null {
  const prov = assertion.provenance;
  if (!prov) return null;

  // 1. Prefer temporal timestamps
  if (prov.temporal_start_ms != null) {
    return {
      start: prov.temporal_start_ms / 1000,
      end: prov.temporal_end_ms != null ? prov.temporal_end_ms / 1000 : prov.temporal_start_ms / 1000,
    };
  }

  // 2. Fall back to chunk_id -> segment mapping
  if (!prov.chunk_id) return null;
  const idx = parseChunkIndex(prov.chunk_id);
  if (idx === null) return null;

  return chunkMap.get(idx) ?? null;
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
    const chunkMap = buildChunkTimestampMap(transcription);
    const markers: TimelineMarker[] = [];

    // If an assertion is selected, only show that assertion's marker
    if (selectedAssertionId) {
      for (const a of assertions) {
        const aid = a.assertion_id ?? `${a.subject_text}_${a.predicate}_${a.object_text}`;
        if (aid !== selectedAssertionId) continue;

        const ts = getAssertionTimestamp(a, chunkMap);
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
      const ts = getMentionTimestamp(m, chunkMap);
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
        const ts = getAssertionTimestamp(a, chunkMap);
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
