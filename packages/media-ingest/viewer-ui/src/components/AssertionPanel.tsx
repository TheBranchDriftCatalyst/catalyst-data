import { useState, useMemo } from "react";
import {
  Badge,
  Input,
  Progress,
  ScrollArea,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@thebranchdriftcatalyst/catalyst-ui";
import { Search, MessageSquareQuote, ChevronRight } from "lucide-react";
import type { Assertion } from "@/types/media";
import { cn } from "@/lib/utils";

interface AssertionPanelProps {
  assertions: Assertion[];
  onAssertionSelect?: (assertionId: string | null) => void;
  selectedAssertionId?: string | null;
  className?: string;
}

type SortField = "confidence" | "predicate" | "subject";

export default function AssertionPanel({ assertions, onAssertionSelect, selectedAssertionId, className = "" }: AssertionPanelProps) {
  const [sortBy, setSortBy] = useState<SortField>("confidence");
  const [sortAsc, setSortAsc] = useState(false);
  const [filterText, setFilterText] = useState("");

  const sorted = useMemo(() => {
    let filtered = assertions;
    if (filterText) {
      const q = filterText.toLowerCase();
      filtered = assertions.filter(
        (a) =>
          a.subject_text.toLowerCase().includes(q) ||
          a.predicate.toLowerCase().includes(q) ||
          a.object_text.toLowerCase().includes(q),
      );
    }

    return [...filtered].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "confidence":
          cmp = a.confidence - b.confidence;
          break;
        case "predicate":
          cmp = a.predicate_canonical.localeCompare(b.predicate_canonical);
          break;
        case "subject":
          cmp = a.subject_text.localeCompare(b.subject_text);
          break;
      }
      return sortAsc ? cmp : -cmp;
    });
  }, [assertions, sortBy, sortAsc, filterText]);

  const handleSort = (field: SortField) => {
    if (sortBy === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortBy(field);
      setSortAsc(false);
    }
  };

  const sortIcon = (field: SortField) => {
    if (sortBy !== field) return null;
    return <span className="ml-1 text-[10px]">{sortAsc ? "\u25B2" : "\u25BC"}</span>;
  };

  if (assertions.length === 0) {
    return (
      <div
        className={cn("flex flex-col items-center justify-center gap-2 text-zinc-500", className)}
      >
        <MessageSquareQuote className="h-6 w-6 text-zinc-700" />
        <p className="text-sm">No assertions extracted</p>
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col", className)}>
      {/* Filter */}
      <div className="p-2 border-b border-white/5 flex-shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500" />
          <Input
            type="text"
            placeholder="Filter assertions..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="pl-8 h-7 text-xs bg-surface-2 border-white/5"
          />
        </div>
      </div>

      {/* Table */}
      <ScrollArea className="flex-1">
        <Table>
          <TableHeader>
            <TableRow interactive={false}>
              <TableHead
                className="cursor-pointer hover:text-zinc-300 transition-colors text-xs"
                onClick={() => handleSort("subject")}
              >
                Subject{sortIcon("subject")}
              </TableHead>
              <TableHead
                className="cursor-pointer hover:text-zinc-300 transition-colors text-xs"
                onClick={() => handleSort("predicate")}
              >
                Predicate{sortIcon("predicate")}
              </TableHead>
              <TableHead className="text-xs">Object</TableHead>
              <TableHead
                className="cursor-pointer hover:text-zinc-300 transition-colors text-right text-xs w-20"
                onClick={() => handleSort("confidence")}
              >
                Conf{sortIcon("confidence")}
              </TableHead>
              <TableHead className="text-xs w-20 text-center">Flags</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((assertion, i) => {
              const aid = assertion.assertion_id ?? `${assertion.subject_text}_${assertion.predicate}_${assertion.object_text}`;
              return (
                <AssertionRow
                  key={i}
                  assertion={assertion}
                  isSelected={selectedAssertionId === aid}
                  onSelect={() => {
                    if (onAssertionSelect) {
                      onAssertionSelect(selectedAssertionId === aid ? null : aid);
                    }
                  }}
                />
              );
            })}
          </TableBody>
        </Table>
      </ScrollArea>

      {/* Summary footer */}
      <div className="px-3 py-2 text-[10px] text-zinc-600 border-t border-white/5 flex-shrink-0">
        {sorted.length} of {assertions.length} assertions
      </div>
    </div>
  );
}

function AssertionRow({
  assertion,
  isSelected,
  onSelect,
}: {
  assertion: Assertion;
  isSelected?: boolean;
  onSelect?: () => void;
}) {
  const [showQualifiers, setShowQualifiers] = useState(false);
  const qualifierEntries = Object.entries(assertion.qualifiers);
  const hasQualifiers = qualifierEntries.length > 0;

  const confidenceVariant =
    assertion.confidence > 0.8
      ? undefined
      : assertion.confidence > 0.5
        ? ("secondary" as const)
        : ("destructive" as const);

  return (
    <Collapsible open={showQualifiers} onOpenChange={setShowQualifiers}>
      <TableRow
        interactive
        className={cn(
          hasQualifiers ? "cursor-pointer" : "",
          isSelected && "bg-white/[0.08] ring-1 ring-inset ring-white/10",
        )}
        onClick={onSelect}
      >
        {/* Subject */}
        <TableCell className="text-xs text-zinc-200 font-medium">
          {assertion.subject_text}
        </TableCell>

        {/* Predicate */}
        <TableCell className="text-xs">
          <span className="text-zinc-400">{assertion.predicate}</span>
          {assertion.predicate !== assertion.predicate_canonical && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="text-zinc-600 text-[10px] ml-1 cursor-help">*</span>
              </TooltipTrigger>
              <TooltipContent>Canonical: {assertion.predicate_canonical}</TooltipContent>
            </Tooltip>
          )}
        </TableCell>

        {/* Object */}
        <TableCell className="text-xs text-zinc-300">{assertion.object_text}</TableCell>

        {/* Confidence */}
        <TableCell>
          <div className="flex items-center justify-end gap-1.5">
            <Progress
              value={assertion.confidence * 100}
              variant={confidenceVariant}
              className="w-10 h-1.5"
            />
            <span className="text-[10px] tabular-nums text-zinc-500 min-w-[28px] text-right font-mono">
              {(assertion.confidence * 100).toFixed(0)}%
            </span>
          </div>
        </TableCell>

        {/* Flags */}
        <TableCell>
          <CollapsibleTrigger asChild>
            <div className="flex items-center gap-1 justify-center">
              {assertion.negated && (
                <Badge variant="destructive" className="text-[9px] px-1 py-0 h-4">
                  NEG
                </Badge>
              )}
              {assertion.hedged && (
                <Badge
                  variant="outline"
                  className="text-[9px] px-1 py-0 h-4 text-yellow-300 border-yellow-800/50"
                >
                  HEDGED
                </Badge>
              )}
              {hasQualifiers && (
                <ChevronRight
                  className={cn(
                    "h-3 w-3 text-zinc-600 transition-transform",
                    showQualifiers && "rotate-90",
                  )}
                />
              )}
            </div>
          </CollapsibleTrigger>
        </TableCell>
      </TableRow>

      {/* Qualifier expansion row */}
      {hasQualifiers && (
        <CollapsibleContent asChild>
          <tr className="bg-surface-1">
            <td colSpan={5} className="px-6 py-2">
              <div className="flex flex-wrap gap-1.5">
                {qualifierEntries.map(([key, value]) => (
                  <Badge key={key} variant="outline" className="text-[10px] gap-1">
                    <span className="text-zinc-500 font-medium">{key}:</span>
                    <span className="text-zinc-300">{value}</span>
                  </Badge>
                ))}
              </div>
            </td>
          </tr>
        </CollapsibleContent>
      )}
    </Collapsible>
  );
}
