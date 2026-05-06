/** State types for the Ground Truth editor. */

import type { GroundTruthChunk } from "./benchmark";

/**
 * Status of the debounced autosave writer for the GT panel.
 *
 * - `idle`: nothing to save (clean) or never saved this session
 * - `saving`: a PUT is in flight
 * - `saved`: last write succeeded against the Vite dev plugin (writes to disk)
 * - `fallback`: dev plugin unavailable; last write was emitted as a download
 * - `error`: last write attempt threw (network error, etc.)
 *
 * `at` is a `Date.now()` epoch ms used to render an HH:MM:SS indicator.
 */
export type AutosaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "fallback"; at: number }
  | { kind: "error"; message: string };

/**
 * Chunk paired with its index in the underlying `GroundTruthFile.chunks` array.
 * Filtering produces a subset of these so callers can still address the
 * original chunk for selection/keyboard nav without losing position info.
 */
export interface VisibleChunkEntry {
  chunk: GroundTruthChunk;
  origIndex: number;
}
