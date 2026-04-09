import { useState, useMemo } from "react";
import type { Mention } from "@/types/media";

interface EntityPanelProps {
  mentions: Mention[];
  onEntityClick?: (text: string) => void;
  className?: string;
}

interface GroupedEntity {
  text: string;
  count: number;
  contexts: string[];
}

interface MentionGroup {
  type: string;
  entities: GroupedEntity[];
  totalCount: number;
}

const TYPE_COLORS: Record<string, { bg: string; text: string }> = {
  PERSON: { bg: "bg-blue-900/40", text: "text-blue-300" },
  ORG: { bg: "bg-purple-900/40", text: "text-purple-300" },
  GPE: { bg: "bg-green-900/40", text: "text-green-300" },
  LOC: { bg: "bg-emerald-900/40", text: "text-emerald-300" },
  DATE: { bg: "bg-amber-900/40", text: "text-amber-300" },
  EVENT: { bg: "bg-rose-900/40", text: "text-rose-300" },
  PRODUCT: { bg: "bg-cyan-900/40", text: "text-cyan-300" },
  WORK_OF_ART: { bg: "bg-indigo-900/40", text: "text-indigo-300" },
  LAW: { bg: "bg-red-900/40", text: "text-red-300" },
  MONEY: { bg: "bg-yellow-900/40", text: "text-yellow-300" },
  QUANTITY: { bg: "bg-teal-900/40", text: "text-teal-300" },
  NORP: { bg: "bg-fuchsia-900/40", text: "text-fuchsia-300" },
};

const DEFAULT_TYPE_COLOR = { bg: "bg-zinc-800", text: "text-zinc-300" };

function getTypeColor(type: string) {
  return TYPE_COLORS[type.toUpperCase()] ?? DEFAULT_TYPE_COLOR;
}

export default function EntityPanel({
  mentions,
  onEntityClick,
  className = "",
}: EntityPanelProps) {
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());

  const groups = useMemo(() => {
    const typeMap = new Map<string, Map<string, GroupedEntity>>();

    for (const m of mentions) {
      const type = m.mention_type;
      if (!typeMap.has(type)) {
        typeMap.set(type, new Map());
      }
      const entities = typeMap.get(type)!;
      const normalized = m.text.trim();
      if (!entities.has(normalized)) {
        entities.set(normalized, { text: normalized, count: 0, contexts: [] });
      }
      const entity = entities.get(normalized)!;
      entity.count += 1;
      if (entity.contexts.length < 3) {
        entity.contexts.push(m.context);
      }
    }

    const result: MentionGroup[] = [];
    for (const [type, entities] of typeMap) {
      const sorted = Array.from(entities.values()).sort(
        (a, b) => b.count - a.count
      );
      result.push({
        type,
        entities: sorted,
        totalCount: sorted.reduce((sum, e) => sum + e.count, 0),
      });
    }

    return result.sort((a, b) => b.totalCount - a.totalCount);
  }, [mentions]);

  const toggleType = (type: string) => {
    setExpandedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  };

  if (mentions.length === 0) {
    return (
      <div className={`text-zinc-500 text-sm p-3 ${className}`}>
        No entities extracted
      </div>
    );
  }

  return (
    <div className={`overflow-y-auto ${className}`}>
      <div className="space-y-1 p-2">
        {groups.map((group) => {
          const isExpanded = expandedTypes.has(group.type);
          const { bg, text } = getTypeColor(group.type);

          return (
            <div key={group.type} className="rounded-md overflow-hidden">
              {/* Type header */}
              <button
                className={`w-full flex items-center justify-between px-3 py-2 ${bg} hover:brightness-110 transition-all`}
                onClick={() => toggleType(group.type)}
              >
                <span className={`text-xs font-semibold uppercase tracking-wide ${text}`}>
                  {group.type}
                </span>
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] ${text} opacity-70`}>
                    {group.entities.length} unique
                  </span>
                  <span
                    className={`
                      inline-flex items-center justify-center min-w-[20px] h-5 px-1.5
                      rounded-full text-[10px] font-bold
                      ${bg} ${text} border border-current/20
                    `}
                  >
                    {group.totalCount}
                  </span>
                  <svg
                    className={`w-3.5 h-3.5 ${text} transition-transform ${isExpanded ? "rotate-180" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                  </svg>
                </div>
              </button>

              {/* Expanded entity list */}
              {isExpanded && (
                <div className="bg-surface-1 border-x border-b border-white/5">
                  {group.entities.map((entity) => (
                    <button
                      key={entity.text}
                      className="w-full flex items-center justify-between px-3 py-1.5 hover:bg-white/[0.04] transition-colors text-left"
                      onClick={() => onEntityClick?.(entity.text)}
                    >
                      <span className="text-sm text-zinc-300 truncate mr-2">
                        {entity.text}
                      </span>
                      {entity.count > 1 && (
                        <span className="text-[10px] text-zinc-500 tabular-nums flex-shrink-0">
                          x{entity.count}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
