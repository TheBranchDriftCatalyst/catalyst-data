import { useState, useMemo } from "react";
import {
  Badge,
  Input,
  ScrollArea,
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@thebranchdriftcatalyst/catalyst-ui";
import {
  ChevronDown,
  Search,
  User,
  Building2,
  MapPin,
  Calendar,
  Zap,
  BookOpen,
  Scale,
  Banknote,
  Hash,
  Users,
  Tag,
  Crosshair,
} from "lucide-react";
import type { Mention } from "@/types/media";
import { cn } from "@/lib/utils";

interface EntityPanelProps {
  mentions: Mention[];
  onEntityClick?: (text: string) => void;
  onEntitySelect?: (entityText: string | null) => void;
  selectedEntityText?: string | null;
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

const TYPE_CONFIG: Record<string, { icon: React.ElementType; color: string; bg: string }> = {
  PERSON: { icon: User, color: "text-blue-300", bg: "bg-blue-900/40" },
  ORG: { icon: Building2, color: "text-purple-300", bg: "bg-purple-900/40" },
  GPE: { icon: MapPin, color: "text-green-300", bg: "bg-green-900/40" },
  LOC: { icon: MapPin, color: "text-emerald-300", bg: "bg-emerald-900/40" },
  DATE: { icon: Calendar, color: "text-amber-300", bg: "bg-amber-900/40" },
  EVENT: { icon: Zap, color: "text-rose-300", bg: "bg-rose-900/40" },
  PRODUCT: { icon: Tag, color: "text-cyan-300", bg: "bg-cyan-900/40" },
  WORK_OF_ART: {
    icon: BookOpen,
    color: "text-indigo-300",
    bg: "bg-indigo-900/40",
  },
  LAW: { icon: Scale, color: "text-red-300", bg: "bg-red-900/40" },
  MONEY: { icon: Banknote, color: "text-yellow-300", bg: "bg-yellow-900/40" },
  QUANTITY: { icon: Hash, color: "text-teal-300", bg: "bg-teal-900/40" },
  NORP: { icon: Users, color: "text-fuchsia-300", bg: "bg-fuchsia-900/40" },
};

const DEFAULT_CONFIG = {
  icon: Crosshair,
  color: "text-zinc-300",
  bg: "bg-zinc-800",
};

function getTypeConfig(type: string) {
  return TYPE_CONFIG[type.toUpperCase()] ?? DEFAULT_CONFIG;
}

export default function EntityPanel({
  mentions,
  onEntityClick,
  onEntitySelect,
  selectedEntityText,
  className = "",
}: EntityPanelProps) {
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());
  const [searchText, setSearchText] = useState("");

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
        entities.set(normalized, {
          text: normalized,
          count: 0,
          contexts: [],
        });
      }
      const entity = entities.get(normalized)!;
      entity.count += 1;
      if (entity.contexts.length < 3) {
        entity.contexts.push(m.context);
      }
    }

    const result: MentionGroup[] = [];
    for (const [type, entities] of typeMap) {
      const sorted = Array.from(entities.values()).sort((a, b) => b.count - a.count);
      result.push({
        type,
        entities: sorted,
        totalCount: sorted.reduce((sum, e) => sum + e.count, 0),
      });
    }

    return result.sort((a, b) => b.totalCount - a.totalCount);
  }, [mentions]);

  const filteredGroups = useMemo(() => {
    if (!searchText) return groups;
    const q = searchText.toLowerCase();
    return groups
      .map((g) => ({
        ...g,
        entities: g.entities.filter((e) => e.text.toLowerCase().includes(q)),
      }))
      .filter((g) => g.entities.length > 0);
  }, [groups, searchText]);

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
      <div
        className={cn("flex flex-col items-center justify-center gap-2 text-zinc-500", className)}
      >
        <Crosshair className="h-6 w-6 text-zinc-700" />
        <p className="text-sm">No entities extracted</p>
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col", className)}>
      {/* Search filter */}
      <div className="p-2 border-b border-white/5 flex-shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500" />
          <Input
            type="text"
            placeholder="Filter entities..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="pl-8 h-7 text-xs bg-surface-2 border-white/5"
          />
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-0.5 p-2">
          {filteredGroups.map((group) => {
            const isExpanded = expandedTypes.has(group.type);
            const config = getTypeConfig(group.type);
            const Icon = config.icon;

            return (
              <Collapsible
                key={group.type}
                open={isExpanded}
                onOpenChange={() => toggleType(group.type)}
              >
                <CollapsibleTrigger className="w-full">
                  <div
                    className={cn(
                      "flex items-center justify-between px-3 py-2 rounded-md transition-all hover:brightness-110",
                      config.bg,
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Icon className={cn("h-3.5 w-3.5", config.color)} />
                      <span
                        className={cn(
                          "text-xs font-semibold uppercase tracking-wide",
                          config.color,
                        )}
                      >
                        {group.type}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={cn("text-[10px] opacity-70", config.color)}>
                        {group.entities.length} unique
                      </span>
                      <Badge
                        variant="secondary"
                        className="text-[10px] px-1.5 py-0 h-5 tabular-nums"
                      >
                        {group.totalCount}
                      </Badge>
                      <ChevronDown
                        className={cn(
                          "h-3.5 w-3.5 transition-transform",
                          config.color,
                          isExpanded && "rotate-180",
                        )}
                      />
                    </div>
                  </div>
                </CollapsibleTrigger>

                <CollapsibleContent>
                  <div className="bg-surface-1/50 border-x border-b border-white/5 rounded-b-md overflow-hidden">
                    {group.entities.map((entity) => (
                      <Tooltip key={entity.text}>
                        <TooltipTrigger asChild>
                          <button
                            className={cn(
                              "w-full flex items-center justify-between px-3 py-1.5 hover:bg-white/[0.04] transition-colors text-left group",
                              selectedEntityText?.toLowerCase() === entity.text.toLowerCase() &&
                                "bg-white/[0.08] ring-1 ring-inset ring-white/10",
                            )}
                            onClick={() => {
                              onEntityClick?.(entity.text);
                              if (onEntitySelect) {
                                onEntitySelect(
                                  selectedEntityText?.toLowerCase() === entity.text.toLowerCase()
                                    ? null
                                    : entity.text,
                                );
                              }
                            }}
                          >
                            <span
                              className={cn(
                                "text-sm truncate mr-2 transition-colors",
                                selectedEntityText?.toLowerCase() === entity.text.toLowerCase()
                                  ? "text-zinc-100 font-medium"
                                  : "text-zinc-300 group-hover:text-zinc-100",
                              )}
                            >
                              {entity.text}
                            </span>
                            {entity.count > 1 && (
                              <Badge
                                variant="outline"
                                className="text-[10px] px-1 py-0 h-4 tabular-nums flex-shrink-0"
                              >
                                x{entity.count}
                              </Badge>
                            )}
                          </button>
                        </TooltipTrigger>
                        {entity.contexts.length > 0 && (
                          <TooltipContent side="left" className="max-w-xs">
                            <p className="text-xs font-medium mb-1">Context:</p>
                            <p className="text-xs text-zinc-400 line-clamp-3">
                              ...{entity.contexts[0]}...
                            </p>
                          </TooltipContent>
                        )}
                      </Tooltip>
                    ))}
                  </div>
                </CollapsibleContent>
              </Collapsible>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}
