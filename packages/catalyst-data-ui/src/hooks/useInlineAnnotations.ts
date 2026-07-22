import { useMemo } from "react";
import type { Segment, Mention } from "@/types/media";

export interface InlineAnnotation {
  start: number; // char offset within segment text
  end: number;
  text: string;
  entityType: string;
  confidence: number;
  mentionId: string;
}

/**
 * Build a map from segment index to inline annotations.
 *
 * Because `span_start`/`span_end` are offsets within chunks (not segments),
 * we use **text matching**: for each unique mention text we scan segments with
 * case-insensitive `indexOf` to locate occurrences.
 *
 * Overlapping annotations are resolved by keeping the longer match when one
 * span fully contains another.
 */
export function useInlineAnnotations(
  segments: Segment[],
  mentions: Mention[] | undefined,
): Map<number, InlineAnnotation[]> {
  return useMemo(() => {
    const result = new Map<number, InlineAnnotation[]>();
    if (!mentions || mentions.length === 0 || segments.length === 0) return result;

    // ── 1. Deduplicate mentions by text + canonical_type ────────────
    const uniqueMap = new Map<string, { text: string; mentionType: string; confidence: number }>();
    for (const m of mentions) {
      const key = `${m.text.toLowerCase()}::${m.canonical_type}`;
      const existing = uniqueMap.get(key);
      const conf = m.provenance?.confidence ?? 0;
      if (!existing || conf > existing.confidence) {
        uniqueMap.set(key, {
          text: m.text,
          mentionType: m.canonical_type,
          confidence: conf,
        });
      }
    }
    const uniqueMentions = Array.from(uniqueMap.values());

    // ── 2. For each segment, find matching mentions ─────────────────
    for (let segIdx = 0; segIdx < segments.length; segIdx++) {
      const segText = segments[segIdx]!.text;
      if (!segText) continue;

      const annotations: InlineAnnotation[] = [];
      const segLower = segText.toLowerCase();

      for (const um of uniqueMentions) {
        const mentionLower = um.text.toLowerCase();
        // Skip very short mention texts (single chars produce noise)
        if (mentionLower.length < 2) continue;

        // Find all occurrences within the segment
        let searchFrom = 0;
        while (searchFrom < segLower.length) {
          const idx = segLower.indexOf(mentionLower, searchFrom);
          if (idx === -1) break;

          annotations.push({
            start: idx,
            end: idx + um.text.length,
            text: segText.slice(idx, idx + um.text.length), // preserve original casing
            entityType: um.mentionType,
            confidence: um.confidence,
            mentionId: `${um.mentionType}::${um.text}`,
          });

          searchFrom = idx + um.text.length;
        }
      }

      if (annotations.length === 0) continue;

      // ── 3. Resolve overlaps: keep longer spans that subsume shorter ones ──
      const resolved = resolveOverlaps(annotations);

      // ── 4. Sort by start position ────────────────────────────────────
      resolved.sort((a, b) => a.start - b.start);

      result.set(segIdx, resolved);
    }

    return result;
  }, [segments, mentions]);
}

/**
 * When two annotations overlap, keep the longer one (it subsumes the shorter).
 * If they partially overlap but neither contains the other, keep both but
 * truncate the shorter one.
 */
function resolveOverlaps(annotations: InlineAnnotation[]): InlineAnnotation[] {
  if (annotations.length <= 1) return annotations;

  // Sort by start, then by length descending (longer first)
  const sorted = [...annotations].sort((a, b) => {
    if (a.start !== b.start) return a.start - b.start;
    return b.end - b.start - (a.end - a.start);
  });

  const kept: InlineAnnotation[] = [];

  for (const ann of sorted) {
    // Check if any kept annotation fully contains this one
    const subsumed = kept.some((k) => k.start <= ann.start && k.end >= ann.end);
    if (subsumed) continue;

    // Remove any kept annotations that this one fully contains
    for (let i = kept.length - 1; i >= 0; i--) {
      const k = kept[i]!;
      if (ann.start <= k.start && ann.end >= k.end) {
        kept.splice(i, 1);
      }
    }

    // Check for partial overlaps with kept annotations — skip if overlapping
    const partialOverlap = kept.some((k) => ann.start < k.end && ann.end > k.start);
    if (partialOverlap) continue;

    kept.push(ann);
  }

  return kept;
}
