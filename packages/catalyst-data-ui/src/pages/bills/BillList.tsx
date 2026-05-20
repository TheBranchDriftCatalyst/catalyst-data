import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Landmark, Search, Scale, CheckCircle2, Users, Calendar, Tag } from "lucide-react";
import { Badge, Card, CardContent, Input, ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { useState, useMemo } from "react";
import { fetchBills } from "@/api/client";
import type { BillListItem } from "@/types/bills";
import { cn } from "@/lib/utils";

/** Grid of bill cards driven by `/viewer/api/congress/bills`.
 *
 *  Each card is a click target into `/bills/:partition`. Fields shown
 *  prefer the silver `bill_document` metadata that the list endpoint
 *  already folded in — no per-card extra fetch.
 *
 *  Filtering is local (the corpus is small enough that round-trip
 *  filter doesn't earn its weight yet); when the bill count grows
 *  past a few hundred, hoist the filter into the API. */
export default function BillList() {
  const [filter, setFilter] = useState("");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["bills", "congress"],
    queryFn: () => fetchBills("congress"),
  });

  const filtered = useMemo(() => {
    if (!data) return [];
    if (!filter.trim()) return data;
    const needle = filter.toLowerCase();
    return data.filter((b) => {
      const hay = [
        b.partition,
        b.title ?? "",
        b.metadata?.policy_area ?? "",
        b.metadata?.bill_type ?? "",
        b.metadata?.sponsor_bioguide ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [data, filter]);

  return (
    <ScrollArea className="flex-1">
      <div data-testid="bill-list" className="p-6 max-w-[1400px] mx-auto space-y-4">
        {/* Header */}
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1
              className="text-2xl font-bold text-zinc-100 tracking-tight flex items-center gap-2"
              style={{ fontFamily: "var(--font-display)" }}
            >
              <Landmark className="h-6 w-6 text-cyan-400" />
              Bills
            </h1>
            <p className="text-xs text-zinc-500 mt-1">
              Congress bills materialised through{" "}
              <span className="font-mono text-zinc-400">bill_detail</span> →{" "}
              <span className="font-mono text-zinc-400">bill_assertions</span>.
              {data && (
                <>
                  {" "}
                  <span className="text-zinc-400">
                    {filtered.length} / {data.length}
                  </span>{" "}
                  showing
                </>
              )}
            </p>
          </div>
          <div className="relative w-72">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-zinc-500" />
            <Input
              className="pl-8 h-8 text-xs"
              placeholder="Filter by title, partition, policy area…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              data-testid="bill-filter"
            />
          </div>
        </div>

        {/* States */}
        {isLoading && (
          <div className="text-zinc-500 text-sm" data-testid="bill-list-loading">
            Loading bills…
          </div>
        )}
        {isError && (
          <div className="text-red-400 text-sm">Failed to load: {(error as Error)?.message}</div>
        )}
        {data && filtered.length === 0 && (
          <div className="text-zinc-600 text-sm italic">No matches.</div>
        )}

        {/* Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((bill) => (
            <BillCard key={bill.partition} bill={bill} />
          ))}
        </div>
      </div>
    </ScrollArea>
  );
}

function BillCard({ bill }: { bill: BillListItem }) {
  const meta = bill.metadata ?? {};
  const congress = meta.congress;
  const billType = meta.bill_type?.toUpperCase();
  const chamber = meta.origin_chamber;
  const sponsor = meta.sponsor_bioguide;
  const cosponsorCount = meta.cosponsor_count;
  const policyArea = meta.policy_area;
  const introduced = meta.introduced_date;
  const becameLaw = meta.became_law;

  return (
    <Link
      to={`/bills/${encodeURIComponent(bill.partition)}`}
      className="block group focus:outline-none focus:ring-2 focus:ring-cyan-500/50 rounded"
      data-testid={`bill-card-${bill.partition}`}
    >
      <Card
        interactive={false}
        className={cn(
          "h-full hover:bg-white/[0.03] transition-colors border-white/5",
          becameLaw && "border-emerald-900/40",
        )}
      >
        <CardContent className="p-4 space-y-3">
          {/* Partition + chamber + bill type chips */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <Badge
              variant="outline"
              className="font-mono text-[10px] px-1.5 py-0 h-4 text-cyan-300 border-cyan-800/40"
            >
              {bill.partition}
            </Badge>
            {congress && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
                {congress}th
              </Badge>
            )}
            {billType && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
                {billType}
              </Badge>
            )}
            {chamber && <span className="text-[10px] text-zinc-500 font-mono">{chamber}</span>}
            {becameLaw && (
              <Badge
                variant="outline"
                className="text-[10px] px-1.5 py-0 h-4 text-emerald-300 border-emerald-800/50 ml-auto flex items-center gap-1"
              >
                <CheckCircle2 className="h-2.5 w-2.5" />
                law
              </Badge>
            )}
          </div>

          {/* Title */}
          <h2
            className="text-sm font-medium text-zinc-100 leading-snug line-clamp-3 group-hover:text-cyan-200 transition-colors"
            style={{ fontFamily: "var(--font-display)" }}
          >
            {bill.title || <span className="text-zinc-600">(untitled)</span>}
          </h2>

          {/* Metadata grid */}
          <div className="grid grid-cols-1 gap-1 text-[10px] font-mono text-zinc-500">
            {sponsor && (
              <div className="flex items-center gap-1.5">
                <Scale className="h-3 w-3" />
                <span>sponsor</span>
                <span className="text-zinc-300">{sponsor}</span>
              </div>
            )}
            {typeof cosponsorCount === "number" && (
              <div className="flex items-center gap-1.5">
                <Users className="h-3 w-3" />
                <span>cosponsors</span>
                <span className="text-zinc-300">{cosponsorCount}</span>
              </div>
            )}
            {introduced && (
              <div className="flex items-center gap-1.5">
                <Calendar className="h-3 w-3" />
                <span>introduced</span>
                <span className="text-zinc-300">{introduced}</span>
              </div>
            )}
            {policyArea && (
              <div className="flex items-center gap-1.5 truncate">
                <Tag className="h-3 w-3 flex-shrink-0" />
                <span className="flex-shrink-0">policy</span>
                <span className="text-zinc-300 truncate">{policyArea}</span>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
