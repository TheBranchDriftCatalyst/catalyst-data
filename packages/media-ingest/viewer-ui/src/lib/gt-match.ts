/**
 * gt-match — fuzzy-match a predicted mention against a ground-truth list.
 *
 * Matches the spirit of compute_model_scores() in
 * tests/shared/extraction_scoring.py — strict matching is on
 * (canonical_text.lower(), canonical_type) with span tolerance for the
 * span-level confirmation. The scoring code itself doesn't use spans for
 * the strict TP set (it uses text+type tuples), but for the State
 * Inspector chip we want a stronger signal: same text, same type, AND
 * spans overlap within ±2 chars. That way "taxpayer" appearing in two
 * different contexts in the same doc isn't both confirmed off a single
 * GT row.
 *
 * Tolerance: ±2 chars on both ends. This absorbs the common drift from
 * encoder span-clipping (leading/trailing whitespace, punctuation),
 * which is the same drift score_mentions() ignores when computing
 * relaxed_f1.
 */

import type { GtMention } from "@/hooks/useRunReport";

const SPAN_TOLERANCE = 2;

export interface PredMention {
  text: string;
  /** canonical_type | mention_type — caller passes whichever it has. */
  mention_type?: string;
  span_start?: number | null;
  span_end?: number | null;
  /** Optional doc/chunk scoping — when present, only GT rows in the
   *  same chunk are considered. The active GT is doc-keyed, so falling
   *  back to doc when chunk isn't available is fine. */
  doc_id?: string;
  chunk_id?: string;
}

function _norm(text: string | undefined | null): string {
  return (text ?? "").trim().toLowerCase();
}

/** Returns ``true`` iff some GT row has matching normalized text + type
 *  (case-insensitive) AND, when both sides have spans, the spans overlap
 *  within ±2 chars on each end. When the prediction has no span, we
 *  match on text+type alone (consistent with the strict-set scoring). */
export function matchesGtMention(pred: PredMention, gtList: GtMention[]): boolean {
  if (gtList.length === 0) return false;
  const predText = _norm(pred.text);
  const predType = _norm(pred.mention_type);
  const ps = pred.span_start;
  const pe = pred.span_end;

  for (const gt of gtList) {
    if (_norm(gt.text) !== predText) continue;
    // Allow match when either side hasn't tagged the type — only block
    // when both sides assert a type and they disagree.
    const gtType = _norm(gt.mention_type);
    if (predType && gtType && predType !== gtType) continue;

    // Optional chunk scoping. If both sides know their chunk, require
    // a match — same span text in different chunks is a different
    // mention, not a confirmation.
    if (pred.chunk_id && gt.chunk_id && pred.chunk_id !== gt.chunk_id) continue;
    if (!pred.chunk_id && pred.doc_id && gt.doc_id && pred.doc_id !== gt.doc_id) continue;

    // Span tolerance — only enforced when both sides have spans.
    if (ps != null && pe != null && gt.span_start != null && gt.span_end != null) {
      if (
        Math.abs(ps - gt.span_start) > SPAN_TOLERANCE ||
        Math.abs(pe - gt.span_end) > SPAN_TOLERANCE
      ) {
        continue;
      }
    }
    return true;
  }
  return false;
}
