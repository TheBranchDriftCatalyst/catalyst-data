/**
 * Shared mention-row styling helpers.
 *
 * Centralises the type-badge palette so all panels (ConsensusDetail,
 * NerEncoderDetail, StateInspector pruned_window) render the same colour
 * for a given canonical_type. Previously this was redefined in
 * ConsensusDetail and rendered inconsistently elsewhere (NerEncoder used a
 * single violet pill, the pruned_window table used violet text).
 */

/** Badge colour for canonical_type — matches the existing mention pill palette. */
export function typeBadgeClass(t: string): string {
  const key = t.toUpperCase();
  if (key === "PERSON") return "bg-blue-500/15 text-blue-200 border-blue-500/30";
  if (key === "ORG" || key === "ORGANIZATION")
    return "bg-violet-500/15 text-violet-200 border-violet-500/30";
  if (key === "GPE" || key === "LOCATION" || key === "LOC" || key === "FAC")
    return "bg-emerald-500/15 text-emerald-200 border-emerald-500/30";
  if (key === "DATE" || key === "TIME" || key === "TEMPORAL")
    return "bg-amber-500/15 text-amber-200 border-amber-500/30";
  return "bg-zinc-500/15 text-zinc-300 border-zinc-500/30";
}

/** Format type_votes dict as "PERSON×4, GPE×1" sorted by count desc. */
export function fmtTypeVotes(tv: Record<string, number>): string {
  return Object.entries(tv)
    .sort(([, a], [, b]) => b - a)
    .map(([t, n]) => `${t}×${n}`)
    .join(", ");
}
