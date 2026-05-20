import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Landmark,
  Scale,
  Users,
  Calendar,
  CheckCircle2,
  Tag,
  FileText,
  MessageSquareQuote,
  Database,
  ExternalLink,
  AlertCircle,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  ScrollArea,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@thebranchdriftcatalyst/catalyst-ui";
import { fetchBill, fetchBillAsset } from "@/api/client";
import type { BillChunk } from "@/types/bills";
import type { Assertion } from "@/types/contracts";
import AssertionPanel from "@/components/AssertionPanel";
import { useSelection } from "@/contexts/SelectionContext";
import { useMemo } from "react";
import { cn } from "@/lib/utils";

/** Per-bill viewer surface backed by the partitioned-resource API.
 *
 *  Tabs:
 *   - Overview: full bill text + sections + metadata (silver bill_document)
 *   - Assertions: AssertionPanel on gold bill_assertions (AMR-rich)
 *   - Structured: AssertionPanel on gold congress_structured_assertions
 *     (cosponsor + public-law dated rows — note `t_valid_from` populated
 *     where AMR-projected assertions don't have it yet)
 *   - Chunks: ordered silver bill_chunks with text + index
 *
 *  Each tab is its own tanstack-query so they load in parallel and
 *  cache independently. Switching tabs is local-only — no extra
 *  network. */
export default function BillDetail() {
  const { partition } = useParams<{ partition: string }>();
  // Hooks must run unconditionally — the `enabled` flag short-circuits
  // the network call when the route param is somehow missing without
  // breaking hooks ordering.
  const partitionKey = partition ?? "";
  const enabled = Boolean(partition);

  const billQuery = useQuery({
    queryKey: ["bill", "congress", partitionKey],
    queryFn: () => fetchBill("congress", partitionKey),
    enabled,
  });

  const assertionsQuery = useQuery({
    queryKey: ["bill", "congress", partitionKey, "assertions"],
    queryFn: () => fetchBillAsset<Assertion>("congress", partitionKey, "assertions"),
    enabled,
  });

  const structuredQuery = useQuery({
    queryKey: ["bill", "congress", partitionKey, "structured"],
    queryFn: () => fetchBillAsset<Assertion>("congress", partitionKey, "structured"),
    enabled,
  });

  const chunksQuery = useQuery({
    queryKey: ["bill", "congress", partitionKey, "chunks"],
    queryFn: () => fetchBillAsset<BillChunk>("congress", partitionKey, "chunks"),
    enabled,
  });

  // Selection plumbing — must run unconditionally before any early
  // returns to satisfy react-hooks/rules-of-hooks.
  const { selection, selectAssertion, selectChunk, clear } = useSelection();
  const bill = billQuery.data;
  const chunksRowsRaw = chunksQuery.data?.rows;
  const chunks = useMemo<BillChunk[]>(() => chunksRowsRaw ?? [], [chunksRowsRaw]);
  const assertions = assertionsQuery.data?.rows ?? [];
  const structured = structuredQuery.data?.rows ?? [];
  const selectionContext = useMemo(
    () => ({ partition: partitionKey, domain: "congress", bill, chunks }),
    [partitionKey, bill, chunks],
  );

  if (!partition) {
    return <ErrorBlock title="Missing partition param" />;
  }
  if (billQuery.isLoading) {
    return (
      <div className="p-6 text-zinc-500 text-sm" data-testid="bill-detail-loading">
        Loading bill…
      </div>
    );
  }
  if (billQuery.isError || !bill) {
    return <ErrorBlock title="Failed to load bill" message={(billQuery.error as Error)?.message} />;
  }

  const meta = bill.metadata ?? {};
  // Currently-selected assertion id (drives the highlight ring on the
  // card). Null when nothing assertion-shaped is selected.
  const selectedAssertionId =
    selection.kind === "assertion" ? selection.assertion.assertion_id : null;

  const handleAssertionSelect = (id: string | null) => {
    if (!id) {
      clear();
      return;
    }
    const all = [...assertions, ...structured];
    const found = all.find((a) => a.assertion_id === id);
    if (found) selectAssertion(found, selectionContext);
  };

  // S3 Explorer deep-link to the silver row's containing folder.
  const silverPrefix = `silver/congress_data/bill/bill_document/${partition}/`;

  return (
    <ScrollArea className="flex-1">
      <div
        data-testid={`bill-detail-${partition}`}
        className="p-6 max-w-[1400px] mx-auto space-y-6"
      >
        {/* Header */}
        <div className="space-y-3">
          <Button asChild variant="ghost" size="sm" className="gap-2 text-xs h-7 -ml-2">
            <Link to="/bills" data-testid="bill-detail-back">
              <ArrowLeft className="h-3.5 w-3.5" />
              All bills
            </Link>
          </Button>
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1 space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge
                  variant="outline"
                  className="font-mono text-[10px] text-cyan-300 border-cyan-800/40"
                >
                  {partition}
                </Badge>
                {meta.congress && (
                  <Badge variant="secondary" className="text-[10px]">
                    {meta.congress}th Congress
                  </Badge>
                )}
                {meta.bill_type && (
                  <Badge variant="secondary" className="text-[10px]">
                    {meta.bill_type.toUpperCase()}
                  </Badge>
                )}
                {meta.origin_chamber && (
                  <Badge variant="outline" className="text-[10px]">
                    {meta.origin_chamber}
                  </Badge>
                )}
                {meta.became_law && (
                  <Badge
                    variant="outline"
                    className="text-[10px] text-emerald-300 border-emerald-800/50 gap-1"
                  >
                    <CheckCircle2 className="h-2.5 w-2.5" />
                    became law
                  </Badge>
                )}
              </div>
              <h1
                className="text-2xl font-bold text-zinc-100 tracking-tight leading-snug"
                style={{ fontFamily: "var(--font-display)" }}
              >
                <Landmark className="h-5 w-5 inline mr-2 text-cyan-400 mb-1" />
                {bill.title || <span className="text-zinc-600">(untitled)</span>}
              </h1>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1 text-[11px] font-mono text-zinc-500">
                {meta.sponsor_bioguide && (
                  <MetaRow icon={Scale} label="sponsor" value={meta.sponsor_bioguide} />
                )}
                {typeof meta.cosponsor_count === "number" && (
                  <MetaRow icon={Users} label="cosponsors" value={String(meta.cosponsor_count)} />
                )}
                {meta.introduced_date && (
                  <MetaRow icon={Calendar} label="introduced" value={meta.introduced_date} />
                )}
                {meta.policy_area && <MetaRow icon={Tag} label="policy" value={meta.policy_area} />}
              </div>
            </div>
            <Button asChild variant="outline" size="sm" className="gap-2 text-xs">
              <a
                href={`/viewer/s3?p=${encodeURIComponent(silverPrefix)}`}
                data-testid="bill-detail-silver-link"
              >
                <Database className="h-3.5 w-3.5" />
                Silver row
                <ExternalLink className="h-3 w-3 opacity-60" />
              </a>
            </Button>
          </div>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="overview" className="flex-1">
          <TabsList className="border-b border-white/5 bg-transparent rounded-none p-0 h-auto justify-start">
            <TabTrigger value="overview" icon={FileText} label="Overview" />
            <TabTrigger
              value="assertions"
              icon={MessageSquareQuote}
              label="Assertions"
              count={assertions.length}
              loading={assertionsQuery.isLoading}
            />
            <TabTrigger
              value="structured"
              icon={Scale}
              label="Structured"
              count={structured.length}
              loading={structuredQuery.isLoading}
            />
            <TabTrigger
              value="chunks"
              icon={FileText}
              label="Chunks"
              count={chunks.length}
              loading={chunksQuery.isLoading}
            />
          </TabsList>

          <TabsContent value="overview" className="mt-4 space-y-4">
            <OverviewTab content={bill.content ?? ""} sections={bill.sections ?? {}} />
          </TabsContent>

          <TabsContent value="assertions" className="mt-4">
            <Card interactive={false}>
              <CardContent className="p-0">
                <AssertionPanel
                  assertions={assertions}
                  onAssertionSelect={handleAssertionSelect}
                  selectedAssertionId={selectedAssertionId}
                  className="max-h-[600px]"
                />
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="structured" className="mt-4">
            <Card interactive={false}>
              <CardContent className="p-0">
                <AssertionPanel
                  assertions={structured}
                  onAssertionSelect={handleAssertionSelect}
                  selectedAssertionId={selectedAssertionId}
                  className="max-h-[600px]"
                />
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="chunks" className="mt-4 space-y-3">
            <ChunksTab
              chunks={chunks}
              onChunkSelect={(c) => selectChunk(c, selectionContext)}
              selectedChunkId={selection.kind === "chunk" ? selection.chunk.chunk_id : null}
            />
          </TabsContent>
        </Tabs>
      </div>
    </ScrollArea>
  );
}

function MetaRow({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <Icon className="h-3 w-3 flex-shrink-0" />
      <span className="text-zinc-600 flex-shrink-0">{label}</span>
      <span className="text-zinc-300 truncate">{value}</span>
    </div>
  );
}

function TabTrigger({
  value,
  icon: Icon,
  label,
  count,
  loading,
}: {
  value: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  count?: number;
  loading?: boolean;
}) {
  return (
    <TabsTrigger
      value={value}
      className={cn(
        "data-[state=active]:bg-white/[0.04] data-[state=active]:text-cyan-300",
        "data-[state=active]:border-cyan-500/60 data-[state=active]:shadow-none",
        "border-b-2 border-transparent rounded-none px-3 py-1.5 text-xs font-mono gap-1.5",
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
      {typeof count === "number" && (
        <span className="text-[10px] text-zinc-500 tabular-nums">{loading ? "…" : count}</span>
      )}
    </TabsTrigger>
  );
}

function OverviewTab({
  content,
  sections,
}: {
  content: string;
  sections: Record<string, string | string[]>;
}) {
  return (
    <Card interactive={false}>
      <CardContent className="p-4 space-y-4">
        {Object.entries(sections).map(([label, value]) => (
          <div key={label} className="space-y-1">
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
              {label}
            </div>
            <div className="text-xs text-zinc-300 whitespace-pre-wrap leading-relaxed">
              {Array.isArray(value) ? value.join("\n") : value}
            </div>
          </div>
        ))}
        {content && (
          <div className="space-y-1">
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
              Full text
            </div>
            <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap leading-relaxed font-mono bg-black/30 rounded p-3 max-h-[500px] overflow-auto">
              {content}
            </pre>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ChunksTab({
  chunks,
  onChunkSelect,
  selectedChunkId,
}: {
  chunks: BillChunk[];
  onChunkSelect?: (chunk: BillChunk) => void;
  selectedChunkId?: string | null;
}) {
  if (chunks.length === 0) {
    return <div className="text-zinc-500 text-sm italic">No chunks materialised.</div>;
  }
  return (
    <>
      {chunks.map((chunk) => {
        const selected = chunk.chunk_id === selectedChunkId;
        return (
          <Card
            key={chunk.chunk_id}
            interactive={false}
            data-testid={`chunk-card-${chunk.chunk_id}`}
            className={cn(
              "cursor-pointer hover:bg-white/[0.02] transition-colors",
              selected && "ring-1 ring-cyan-500/60 bg-white/[0.03]",
            )}
            onClick={() => onChunkSelect?.(chunk)}
          >
            <CardContent className="p-3 space-y-2">
              <div className="flex items-center gap-2 text-[10px] font-mono text-zinc-500">
                <Badge variant="outline" className="text-[9px] px-1 py-0 h-4">
                  chunk {chunk.index + 1} / {chunk.total_chunks}
                </Badge>
                <span className="text-zinc-600 truncate">{chunk.chunk_id}</span>
              </div>
              <div className="text-[11px] text-zinc-300 whitespace-pre-wrap leading-relaxed font-mono">
                {chunk.text}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </>
  );
}

function ErrorBlock({ title, message }: { title: string; message?: string }) {
  return (
    <div className="p-8" data-testid="bill-detail-error">
      <Card interactive={false} className="max-w-lg mx-auto mt-8">
        <CardContent className="flex flex-col items-center text-center py-8">
          <div className="rounded-full bg-red-950/50 p-3 mb-4">
            <AlertCircle className="h-6 w-6 text-red-400" />
          </div>
          <h3 className="text-sm font-medium text-zinc-200 mb-1">{title}</h3>
          {message && <p className="text-xs text-zinc-500 mb-4">{message}</p>}
          <Button asChild variant="outline" size="sm">
            <Link to="/bills">Back to bills</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
