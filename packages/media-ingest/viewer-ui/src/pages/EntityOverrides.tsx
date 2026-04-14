import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Input,
  Badge,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  ScrollArea,
} from "@thebranchdriftcatalyst/catalyst-ui";
import { Plus, Trash2, ToggleLeft, ToggleRight, ArrowRightLeft } from "lucide-react";
import {
  fetchEntityOverrides,
  createEntityOverride,
  deleteEntityOverride,
  toggleEntityOverride,
} from "@/api/client";
import type { EntityOverride } from "@/api/client";
import { cn } from "@/lib/utils";

const ENTITY_TYPES = [
  "PERSON",
  "ORG",
  "GPE",
  "LOC",
  "DATE",
  "EVENT",
  "MONEY",
  "LAW",
  "NORP",
  "FACILITY",
  "DOCUMENT",
  "BOOK",
  "ROLE",
  "STRATEGIC_ASSET",
  "FINANCIAL_INSTRUMENT",
  "OTHER",
];

export default function EntityOverrides() {
  const queryClient = useQueryClient();
  const [showAll, setShowAll] = useState(false);
  const [aliasText, setAliasText] = useState("");
  const [targetName, setTargetName] = useState("");
  const [entityType, setEntityType] = useState("PERSON");
  const [notes, setNotes] = useState("");

  const { data: overrides = [], isLoading } = useQuery({
    queryKey: ["entity-overrides", showAll],
    queryFn: () => fetchEntityOverrides(!showAll),
    staleTime: 10_000,
  });

  const createMut = useMutation({
    mutationFn: createEntityOverride,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["entity-overrides"] });
      setAliasText("");
      setTargetName("");
      setNotes("");
    },
  });

  const deleteMut = useMutation({
    mutationFn: deleteEntityOverride,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["entity-overrides"] }),
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      toggleEntityOverride(id, active),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["entity-overrides"] }),
  });

  const handleCreate = () => {
    if (!aliasText.trim() || !targetName.trim()) return;
    createMut.mutate({
      alias_text: aliasText.trim(),
      target_name: targetName.trim(),
      entity_type: entityType,
      notes: notes.trim(),
    });
  };

  // Group by entity_type → target_name
  const grouped = overrides.reduce<Record<string, EntityOverride[]>>((acc, o) => {
    const key = `${o.entity_type}:${o.target_name}`;
    (acc[key] ??= []).push(o);
    return acc;
  }, {});

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-white/5">
        <h1 className="text-lg font-semibold text-zinc-100">Entity Alias Overrides</h1>
        <p className="text-xs text-zinc-500 mt-1">
          Define manual alias merges for the next canonical_entities run. Single-name entities like
          &quot;Trump&quot; can be mapped to their full name &quot;Donald Trump&quot;.
        </p>
      </div>

      {/* Create form */}
      <div className="px-6 py-4 border-b border-white/5 space-y-3">
        <div className="flex items-end gap-3">
          <div className="flex-1 space-y-1">
            <label className="text-[10px] text-zinc-500 uppercase tracking-wide">
              Alias (short form)
            </label>
            <Input
              placeholder='e.g. "Trump"'
              value={aliasText}
              onChange={(e) => setAliasText(e.target.value)}
              className="h-8 text-sm bg-surface-2 border-white/5"
            />
          </div>

          <ArrowRightLeft className="h-4 w-4 text-zinc-600 mb-2 flex-shrink-0" />

          <div className="flex-1 space-y-1">
            <label className="text-[10px] text-zinc-500 uppercase tracking-wide">
              Target (canonical name)
            </label>
            <Input
              placeholder='e.g. "Donald Trump"'
              value={targetName}
              onChange={(e) => setTargetName(e.target.value)}
              className="h-8 text-sm bg-surface-2 border-white/5"
            />
          </div>

          <div className="w-40 space-y-1">
            <label className="text-[10px] text-zinc-500 uppercase tracking-wide">Type</label>
            <Select value={entityType} onValueChange={setEntityType}>
              <SelectTrigger className="h-8 text-xs bg-surface-2 border-white/5">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENTITY_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="flex items-end gap-3">
          <div className="flex-1 space-y-1">
            <label className="text-[10px] text-zinc-500 uppercase tracking-wide">
              Notes (optional)
            </label>
            <Input
              placeholder="Why this override is needed..."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="h-8 text-sm bg-surface-2 border-white/5"
            />
          </div>
          <Button
            size="sm"
            onClick={handleCreate}
            disabled={!aliasText.trim() || !targetName.trim() || createMut.isPending}
            className="h-8"
          >
            <Plus className="h-3.5 w-3.5 mr-1" />
            Add Override
          </Button>
        </div>
      </div>

      {/* Controls */}
      <div className="px-6 py-2 flex items-center justify-between border-b border-white/5">
        <span className="text-xs text-zinc-500">{overrides.length} override(s)</span>
        <Button
          variant="ghost"
          size="sm"
          className="text-xs h-7"
          onClick={() => setShowAll(!showAll)}
        >
          {showAll ? "Active only" : "Show all (incl. disabled)"}
        </Button>
      </div>

      {/* Override list */}
      <ScrollArea className="flex-1">
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <div className="w-5 h-5 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
          </div>
        )}

        {!isLoading && overrides.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-zinc-600">
            <ArrowRightLeft className="h-8 w-8 mb-2" />
            <p className="text-sm">No overrides defined yet</p>
            <p className="text-xs mt-1">
              Add one above to force-merge entities in the next pipeline run
            </p>
          </div>
        )}

        <div className="px-6 py-3 space-y-4">
          {Object.entries(grouped).map(([key, items]) => {
            const first = items[0];
            if (!first) return null;
            const [type, target] = [first.entity_type, first.target_name];
            return (
              <div key={key} className="space-y-1">
                <div className="flex items-center gap-2 mb-1.5">
                  <Badge variant="secondary" className="text-[10px] uppercase">
                    {type}
                  </Badge>
                  <span className="text-sm font-medium text-zinc-200">{target}</span>
                </div>

                {items.map((o) => (
                  <OverrideRow
                    key={o.override_id}
                    override={o}
                    onToggle={(active) => toggleMut.mutate({ id: o.override_id, active })}
                    onDelete={() => deleteMut.mutate(o.override_id)}
                  />
                ))}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}

function OverrideRow({
  override: o,
  onToggle,
  onDelete,
}: {
  override: EntityOverride;
  onToggle: (active: boolean) => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 px-3 py-2 rounded-md border",
        o.is_active
          ? "bg-surface-2/50 border-white/5"
          : "bg-surface-1/30 border-white/[0.02] opacity-50",
      )}
    >
      <span className="text-sm text-zinc-400 flex-shrink-0">&quot;{o.alias_text}&quot;</span>
      <ArrowRightLeft className="h-3 w-3 text-zinc-600 flex-shrink-0" />
      <span className="text-sm text-zinc-200 flex-1">&quot;{o.target_name}&quot;</span>

      {o.notes && (
        <span className="text-[10px] text-zinc-600 max-w-[200px] truncate">{o.notes}</span>
      )}

      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => onToggle(!o.is_active)}
        title={o.is_active ? "Disable" : "Enable"}
      >
        {o.is_active ? (
          <ToggleRight className="h-4 w-4 text-green-400" />
        ) : (
          <ToggleLeft className="h-4 w-4 text-zinc-600" />
        )}
      </Button>

      <Button variant="ghost" size="icon-sm" onClick={onDelete} title="Delete">
        <Trash2 className="h-3.5 w-3.5 text-red-400/60 hover:text-red-400" />
      </Button>
    </div>
  );
}
