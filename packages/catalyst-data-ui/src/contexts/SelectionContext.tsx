/**
 * Cross-page selection state.
 *
 * Any view (Bills detail, media Player, future entity / chunk explorer)
 * can push the currently-focused row into this context. The right-rail
 * <DetailsPanel> reads from it and renders a kind-specific drilldown
 * — provenance, AMR breakdown, frame meaning, "open source chunk", etc.
 *
 * Keeping selection in one place (instead of per-page local state) is
 * what lets the right panel work across page transitions and lets
 * cross-view ergonomics like "click an entity in the Bills detail
 * panel → it links to the Player view" land without re-plumbing each
 * page.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { Assertion, Mention } from "@/types/contracts";
import type { BillChunk, BillDetail } from "@/types/bills";
import type { BillClaim } from "@/types/billClaims";

/** Where the selected row came from — used to render breadcrumbs +
 *  "open source" links back to the originating partition/document. */
export interface SelectionContextInfo {
  /** Dagster partition key when the selection came from a partitioned
   *  asset (e.g. ``119-hres-1``, ``media-tubesync-demo-video-9d04``). */
  partition?: string;
  /** Domain slug (``congress``, ``media``, ``leaks``) — drives the
   *  back-link route + the asset prefix for follow-on fetches. */
  domain?: string;
  /** Optional: the bill detail or media document carrying the selected
   *  row, so the panel can show its title without an extra fetch. */
  bill?: BillDetail;
  /** Cached chunks for the same partition. The "go to chunk N" link in
   *  the panel uses ``sentence_index`` + this list to identify the
   *  source chunk without round-tripping the API. */
  chunks?: BillChunk[];
}

export type Selection =
  | { kind: "none" }
  | { kind: "assertion"; assertion: Assertion; context?: SelectionContextInfo }
  | { kind: "mention"; mention: Mention; context?: SelectionContextInfo }
  | { kind: "chunk"; chunk: BillChunk; context?: SelectionContextInfo }
  | { kind: "claim"; claim: BillClaim; context?: SelectionContextInfo };

interface SelectionState {
  selection: Selection;
  setSelection: (s: Selection) => void;
  selectAssertion: (assertion: Assertion, context?: SelectionContextInfo) => void;
  selectMention: (mention: Mention, context?: SelectionContextInfo) => void;
  selectChunk: (chunk: BillChunk, context?: SelectionContextInfo) => void;
  selectClaim: (claim: BillClaim, context?: SelectionContextInfo) => void;
  clear: () => void;
  /** Convenience: ``true`` when the panel should be open. */
  isOpen: boolean;
}

const SelectionStateContext = createContext<SelectionState | null>(null);

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [selection, setSelection] = useState<Selection>({ kind: "none" });

  const selectAssertion = useCallback(
    (assertion: Assertion, context?: SelectionContextInfo) =>
      setSelection({ kind: "assertion", assertion, context }),
    [],
  );
  const selectMention = useCallback(
    (mention: Mention, context?: SelectionContextInfo) =>
      setSelection({ kind: "mention", mention, context }),
    [],
  );
  const selectChunk = useCallback(
    (chunk: BillChunk, context?: SelectionContextInfo) =>
      setSelection({ kind: "chunk", chunk, context }),
    [],
  );
  const selectClaim = useCallback(
    (claim: BillClaim, context?: SelectionContextInfo) =>
      setSelection({ kind: "claim", claim, context }),
    [],
  );
  const clear = useCallback(() => setSelection({ kind: "none" }), []);

  const value = useMemo<SelectionState>(
    () => ({
      selection,
      setSelection,
      selectAssertion,
      selectMention,
      selectChunk,
      selectClaim,
      clear,
      isOpen: selection.kind !== "none",
    }),
    [selection, selectAssertion, selectMention, selectChunk, selectClaim, clear],
  );

  return <SelectionStateContext.Provider value={value}>{children}</SelectionStateContext.Provider>;
}

export function useSelection(): SelectionState {
  const ctx = useContext(SelectionStateContext);
  if (!ctx) {
    throw new Error("useSelection must be used inside a SelectionProvider");
  }
  return ctx;
}
