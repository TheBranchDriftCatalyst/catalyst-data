import { BookOpen, Code2, FileText, LayoutGrid, ListTree } from "lucide-react";
import type { FileKind } from "../utils";
import type { ViewMode } from "../hooks/useExplorerState";
import type { ViewOption } from "./ViewToggle";

/** Per-FileKind view-mode menu. The first entry is the default when the
 *  user hasn't explicitly picked a `?view=` for the current file. */
export const VIEW_OPTIONS: Record<FileKind, ViewOption[]> = {
  jsonl: [
    { mode: "table", label: "Table", icon: LayoutGrid },
    { mode: "tree", label: "Tree", icon: ListTree },
    { mode: "raw", label: "Raw", icon: FileText },
  ],
  json: [
    { mode: "tree", label: "Tree", icon: ListTree },
    { mode: "raw", label: "Raw", icon: FileText },
  ],
  text: [
    { mode: "markdown", label: "Rendered", icon: BookOpen },
    { mode: "raw", label: "Raw", icon: FileText },
  ],
  code: [
    { mode: "code", label: "Code", icon: Code2 },
    { mode: "raw", label: "Raw", icon: FileText },
  ],
  // Media kinds + binary don't get a toggle — they have one canonical view.
  image: [],
  audio: [],
  video: [],
  binary: [],
};

/** Resolve which view to render: explicit `requested` if it applies to the
 *  given kind, else the kind's default (first option), else null. */
export function resolveView(kind: FileKind, requested: ViewMode | null): ViewMode | null {
  const opts = VIEW_OPTIONS[kind];
  if (opts.length === 0) return null;
  if (requested && opts.some((o) => o.mode === requested)) return requested;
  return opts[0]!.mode;
}
