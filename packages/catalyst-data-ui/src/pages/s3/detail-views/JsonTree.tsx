import { useState, useCallback } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface JsonTreeProps {
  data: unknown;
  /** Depth at which all nodes start collapsed. Top-level always renders open. */
  collapseDepth?: number;
}

/** Collapsible tree view for arbitrary JSON.
 *
 *  Zero deps — uses native lucide chevrons + tailwind. Keys/values are
 *  syntax-colored: strings green, numbers amber, booleans cyan, null gray.
 *  Click a row's chevron (or the key) to expand/collapse children. Top-level
 *  arrays/objects render expanded; deeper subtrees default-collapse at
 *  ``collapseDepth`` so giant fixtures don't blow up the DOM.
 */
export function JsonTree({ data, collapseDepth = 2 }: JsonTreeProps) {
  return (
    <div className="font-mono text-[11px] leading-relaxed text-zinc-300 p-2">
      <Node value={data} keyName={null} depth={0} collapseDepth={collapseDepth} />
    </div>
  );
}

function Node({
  value,
  keyName,
  depth,
  collapseDepth,
}: {
  value: unknown;
  keyName: string | number | null;
  depth: number;
  collapseDepth: number;
}) {
  const [open, setOpen] = useState(depth < collapseDepth);
  const toggle = useCallback(() => setOpen((v) => !v), []);

  const isArray = Array.isArray(value);
  const isObject = !isArray && value !== null && typeof value === "object";

  const keyLabel =
    keyName === null ? null : (
      <span className="text-cyan-400">
        {typeof keyName === "number" ? keyName : JSON.stringify(keyName)}
      </span>
    );

  if (!isArray && !isObject) {
    return (
      <div className="flex" style={{ paddingLeft: depth * 12 }}>
        <span className="w-4 flex-shrink-0" />
        {keyLabel && (
          <>
            {keyLabel}
            <span className="text-zinc-600 mr-1">:</span>
          </>
        )}
        <Leaf value={value} />
      </div>
    );
  }

  const entries = isArray
    ? (value as unknown[]).map((v, i) => [i, v] as const)
    : Object.entries(value as Record<string, unknown>);
  const count = entries.length;
  const opener = isArray ? "[" : "{";
  const closer = isArray ? "]" : "}";

  return (
    <div>
      <div
        className="flex cursor-pointer hover:bg-white/[0.02] rounded"
        style={{ paddingLeft: depth * 12 }}
        onClick={toggle}
      >
        <span className="w-4 flex-shrink-0 text-zinc-600">
          {open ? (
            <ChevronDown className="h-3 w-3 inline" />
          ) : (
            <ChevronRight className="h-3 w-3 inline" />
          )}
        </span>
        {keyLabel && (
          <>
            {keyLabel}
            <span className="text-zinc-600 mr-1">:</span>
          </>
        )}
        <span className="text-zinc-500">{opener}</span>
        {!open && (
          <>
            <span className="text-zinc-600 mx-1 italic">
              {count} {isArray ? (count === 1 ? "item" : "items") : count === 1 ? "key" : "keys"}
            </span>
            <span className="text-zinc-500">{closer}</span>
          </>
        )}
      </div>
      {open && (
        <>
          {entries.map(([k, v]) => (
            <Node
              key={String(k)}
              value={v}
              keyName={k}
              depth={depth + 1}
              collapseDepth={collapseDepth}
            />
          ))}
          <div className="text-zinc-500" style={{ paddingLeft: depth * 12 + 16 }}>
            {closer}
          </div>
        </>
      )}
    </div>
  );
}

function Leaf({ value }: { value: unknown }) {
  if (value === null) return <span className="text-zinc-500">null</span>;
  if (value === undefined) return <span className="text-zinc-500">undefined</span>;
  if (typeof value === "string")
    return <span className="text-green-400 break-all">{JSON.stringify(value)}</span>;
  if (typeof value === "number") return <span className="text-amber-300">{value}</span>;
  if (typeof value === "boolean") return <span className="text-cyan-300">{String(value)}</span>;
  return <span className={cn("text-zinc-400")}>{String(value)}</span>;
}
