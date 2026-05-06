import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export type SearchScope = "prefix" | "bucket";
export type SortKey = "name" | "size" | "modified";
/** View mode for the file preview pane. The pane filters this list down to
 *  the modes that apply to the selected file's kind. */
export type ViewMode = "table" | "tree" | "raw" | "markdown" | "code";

export interface ExplorerState {
  prefix: string;
  selectedKey: string | null;
  query: string;
  scope: SearchScope;
  sort: SortKey;
  sortDesc: boolean;
  view: ViewMode | null;
}

/** Single source of truth for explorer state — backed by URL search params.
 *
 *  Every state change is shareable as a URL. Browser back/forward replays
 *  the user's navigation history without any extra plumbing.
 *
 *  Params:
 *    - `p`     prefix (defaults to "")
 *    - `key`   selected file key
 *    - `q`     search query
 *    - `scope` search scope: `"prefix"` (default) or `"bucket"`
 *    - `sort`  one of "name" | "size" | "modified"
 *    - `desc`  truthy → descending
 */
export function useExplorerState(): ExplorerState & {
  setPrefix: (p: string) => void;
  setSelectedKey: (key: string | null) => void;
  setQuery: (q: string) => void;
  setScope: (s: SearchScope) => void;
  setSort: (s: SortKey, desc?: boolean) => void;
  setView: (v: ViewMode | null) => void;
  toggleScope: () => void;
} {
  const [params, setParams] = useSearchParams();

  const state: ExplorerState = useMemo(
    () => ({
      prefix: params.get("p") ?? "",
      selectedKey: params.get("key"),
      query: params.get("q") ?? "",
      scope: (params.get("scope") as SearchScope) ?? "prefix",
      sort: (params.get("sort") as SortKey) ?? "name",
      sortDesc: params.get("desc") === "1",
      view: (params.get("view") as ViewMode | null) ?? null,
    }),
    [params],
  );

  const update = useCallback(
    (patch: Record<string, string | null>, options: { push?: boolean } = {}) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === null || v === "") next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        // Default `replace` so frequent updates (typing in search, sort
        // toggles, cursor selection) don't pollute history. Prefix and
        // selection changes opt-in via `push:true` so the browser back
        // button replays the user's navigation trail.
        { replace: !options.push },
      );
    },
    [setParams],
  );

  return {
    ...state,
    setPrefix: useCallback(
      // Also clears the selected key (no longer relevant under a new prefix)
      // AND the search query (cross-prefix searches are surprising when the
      // breadcrumb has changed). Bundled into a single `update()` so both
      // params land in one URL write — two consecutive `setSearchParams`
      // updaters share a stale `prev` and the second silently clobbers the
      // first, which broke folder navigation in the first cut.
      // `push:true` so back/forward replays the navigation trail.
      (p: string) => update({ p, key: null, q: null }, { push: true }),
      [update],
    ),
    setSelectedKey: useCallback((key: string | null) => update({ key }, { push: true }), [update]),
    setQuery: useCallback((q: string) => update({ q }), [update]),
    setScope: useCallback(
      (s: SearchScope) => update({ scope: s === "prefix" ? null : s }),
      [update],
    ),
    setSort: useCallback(
      (s: SortKey, desc?: boolean) =>
        update({
          sort: s === "name" ? null : s,
          desc: desc ? "1" : null,
        }),
      [update],
    ),
    setView: useCallback((v: ViewMode | null) => update({ view: v }), [update]),
    toggleScope: useCallback(() => {
      const next = state.scope === "prefix" ? "bucket" : "prefix";
      update({ scope: next === "prefix" ? null : next });
    }, [state.scope, update]),
  };
}
