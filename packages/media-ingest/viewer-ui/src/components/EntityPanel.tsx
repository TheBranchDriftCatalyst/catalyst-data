import { useState, useMemo, useCallback } from "react";
import {
  Badge,
  Input,
  ScrollArea,
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
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
import type { Mention, AnnotationStatus } from "@/types/media";
import { MentionCard, AnnotationControls, type StatusFilter } from "./domain";
import { cn } from "@/lib/utils";

interface EntityPanelProps {
  mentions: Mention[];
  onEntityClick?: (text: string) => void;
  onEntitySelect?: (entityText: string | null) => void;
  /** Called when a mention is clicked to seek video + scroll transcript. Receives time in seconds. */
  onMentionSeek?: (timeInSeconds: number) => void;
  selectedEntityText?: string | null;
  /** Annotation helpers — optional; panel works without them. */
  getStatus?: (targetId: string) => AnnotationStatus;
  onApprove?: (targetId: string) => void;
  onReject?: (targetId: string) => void;
  onEdit?: (targetId: string, edits: Record<string, unknown>) => void;
  onBulkApprove?: (items: { targetType: "mention"; targetId: string }[]) => void;
  onBulkReject?: (items: { targetType: "mention"; targetId: string }[]) => void;
  className?: string;
}

interface MentionWithId {
  mention: Mention;
  targetId: string;
}

interface MentionGroup {
  type: string;
  mentions: MentionWithId[];
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

/** Produce a stable target ID for a mention. */
function mentionTargetId(m: Mention, index: number): string {
  return `mention_${m.document_id}_${m.chunk_id}_${m.mention_type}_${m.text}_${index}`;
}

export default function EntityPanel({
  mentions,
  onEntityClick,
  onEntitySelect,
  onMentionSeek,
  selectedEntityText,
  getStatus,
  onApprove,
  onReject,
  onEdit,
  onBulkApprove,
  onBulkReject,
  className = "",
}: EntityPanelProps) {
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());
  const [searchText, setSearchText] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  // Build mentions with stable IDs
  const mentionsWithIds = useMemo(
    () => mentions.map((m, i) => ({ mention: m, targetId: mentionTargetId(m, i) })),
    [mentions],
  );

  // Group by type
  const groups = useMemo(() => {
    const typeMap = new Map<string, MentionWithId[]>();

    for (const item of mentionsWithIds) {
      const type = item.mention.mention_type;
      if (!typeMap.has(type)) {
        typeMap.set(type, []);
      }
      typeMap.get(type)!.push(item);
    }

    const result: MentionGroup[] = [];
    for (const [type, items] of typeMap) {
      result.push({ type, mentions: items, totalCount: items.length });
    }

    return result.sort((a, b) => b.totalCount - a.totalCount);
  }, [mentionsWithIds]);

  // Apply search + status filters
  const filteredGroups = useMemo(() => {
    const q = searchText.toLowerCase();
    return groups
      .map((g) => {
        let items = g.mentions;
        if (searchText) {
          items = items.filter((m) => m.mention.text.toLowerCase().includes(q));
        }
        if (statusFilter !== "all" && getStatus) {
          items = items.filter((m) => getStatus(m.targetId) === statusFilter);
        }
        return { ...g, mentions: items, totalCount: items.length };
      })
      .filter((g) => g.mentions.length > 0);
  }, [groups, searchText, statusFilter, getStatus]);

  // Flat list of currently visible target IDs (for bulk actions + counts)
  const visibleTargetIds = useMemo(
    () => filteredGroups.flatMap((g) => g.mentions.map((m) => m.targetId)),
    [filteredGroups],
  );

  // Counts for annotation controls
  const counts = useMemo(() => {
    if (!getStatus) return { approved: 0, rejected: 0, pending: 0, total: visibleTargetIds.length };
    let approved = 0;
    let rejected = 0;
    let pending = 0;
    for (const tid of visibleTargetIds) {
      const s = getStatus(tid);
      if (s === "approved") approved++;
      else if (s === "rejected") rejected++;
      else pending++;
    }
    return { approved, rejected, pending, total: visibleTargetIds.length };
  }, [visibleTargetIds, getStatus]);

  const handleBulkApprove = useCallback(() => {
    if (!getStatus) return;
    const pendingItems = visibleTargetIds
      .filter((tid) => getStatus(tid) === "pending")
      .map((tid) => ({ targetType: "mention" as const, targetId: tid }));
    onBulkApprove?.(pendingItems);
  }, [visibleTargetIds, getStatus, onBulkApprove]);

  const handleBulkReject = useCallback(() => {
    if (!getStatus) return;
    const pendingItems = visibleTargetIds
      .filter((tid) => getStatus(tid) === "pending")
      .map((tid) => ({ targetType: "mention" as const, targetId: tid }));
    onBulkReject?.(pendingItems);
  }, [visibleTargetIds, getStatus, onBulkReject]);

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
    <div data-testid="entity-panel" className={cn("flex flex-col", className)}>
      {/* Search filter */}
      <div className="p-2 border-b border-white/5 flex-shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500" />
          <Input
            data-testid="entity-search"
            type="text"
            placeholder="Filter entities..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="pl-8 h-7 text-xs bg-surface-2 border-white/5"
          />
        </div>
      </div>

      {/* Annotation controls */}
      {getStatus && (
        <AnnotationControls
          counts={counts}
          filter={statusFilter}
          onFilterChange={setStatusFilter}
          onApproveAll={handleBulkApprove}
          onRejectAll={handleBulkReject}
        />
      )}

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
                data-testid={`entity-group-${group.type}`}
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
                  <div className="bg-surface-1/50 border-x border-b border-white/5 rounded-b-md overflow-hidden divide-y divide-white/[0.03]">
                    {group.mentions.map((item) => (
                      <MentionCard
                        key={item.targetId}
                        mention={item.mention}
                        targetId={item.targetId}
                        status={getStatus ? getStatus(item.targetId) : "pending"}
                        onApprove={onApprove}
                        onReject={onReject}
                        onEdit={onEdit}
                        onSeek={onMentionSeek}
                        onClick={(m) => {
                          onEntityClick?.(m.text);
                          if (onEntitySelect) {
                            onEntitySelect(
                              selectedEntityText?.toLowerCase() === m.text.toLowerCase()
                                ? null
                                : m.text,
                            );
                          }
                        }}
                      />
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
