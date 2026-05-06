import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, AlertCircle } from "lucide-react";
import { Badge, Button, Card, CardContent, ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { fetchDomainDocument, fetchDomains } from "@/api/client";
import { JsonTree } from "@/pages/s3/detail-views/JsonTree";

/** Generic detail page for non-media domains. Reads `:domain` and `:id`
 *  from the route, fetches via the per-domain factory endpoint, and
 *  renders a metadata header + a `<JsonTree>` of the full document. A
 *  "View raw silver row" link deep-links into the S3 Explorer at the
 *  exact silver path the row lives in.
 *
 *  Media-ingest documents have their own player surface; this page is
 *  what congress + leaks land on instead. */
export default function DomainDocumentDetail() {
  const { domain: routeSlug, id } = useParams<{ domain: string; id: string }>();

  const docQuery = useQuery({
    queryKey: ["doc", routeSlug, id],
    queryFn: () => fetchDomainDocument(routeSlug!, id!),
    enabled: Boolean(routeSlug && id),
  });

  const domainsQuery = useQuery({
    queryKey: ["domains"],
    queryFn: fetchDomains,
    staleTime: 5 * 60_000,
  });

  if (!routeSlug || !id) {
    return <ErrorBlock title="Missing route params" />;
  }
  if (docQuery.isLoading) {
    return (
      <div className="p-6 text-zinc-500 text-sm" data-testid="doc-detail-loading">
        Loading document…
      </div>
    );
  }
  if (docQuery.isError || !docQuery.data) {
    return (
      <ErrorBlock
        title="Failed to load document"
        message={(docQuery.error as Error)?.message}
        backTo={`/documents/${routeSlug}`}
      />
    );
  }

  const doc = docQuery.data;
  const domain = domainsQuery.data?.find((d) => d.slug === routeSlug);
  // Build the S3 Explorer deep-link to the silver row's containing folder.
  const silverPrefix = domain
    ? `silver/${domain.code_location}/${domain.group}/${domain.asset}/`
    : null;
  const ingestedAt = (doc.metadata?.ingested_at as string | undefined) ?? doc.ingested_at;

  return (
    <ScrollArea className="flex-1">
      <div
        data-testid={`doc-detail-${doc.id}`}
        data-domain={routeSlug}
        className="p-6 max-w-[1400px] mx-auto space-y-6"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <Button asChild variant="ghost" size="sm" className="mb-2 gap-2 text-xs">
              <Link to={`/documents/${routeSlug}`} data-testid="doc-detail-back">
                <ArrowLeft className="h-3.5 w-3.5" />
                {routeSlug}
              </Link>
            </Button>
            <h1
              className="text-2xl font-bold text-zinc-100 tracking-tight truncate"
              style={{ fontFamily: "var(--font-display)" }}
            >
              {doc.title || doc.id}
            </h1>
            <div className="flex flex-wrap items-center gap-2 mt-2 text-xs text-zinc-500">
              {doc.source && (
                <Badge variant="secondary" className="text-[10px]">
                  {doc.source}
                </Badge>
              )}
              {doc.domain && (
                <Badge variant="outline" className="text-[10px]">
                  {doc.domain}
                </Badge>
              )}
              {ingestedAt && <span className="font-mono text-[10px]">ingested {ingestedAt}</span>}
              <span className="font-mono text-[10px]">id={doc.id}</span>
            </div>
          </div>
          {silverPrefix && (
            <Button asChild variant="outline" size="sm" className="gap-2 text-xs">
              <a
                href={`/viewer/s3?p=${encodeURIComponent(silverPrefix)}`}
                data-testid="doc-detail-silver-link"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                View raw silver row
              </a>
            </Button>
          )}
        </div>

        {/* Metadata panel */}
        <Card interactive={false} data-testid="doc-detail-metadata">
          <CardContent className="p-4 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs font-mono">
            <DetailRow label="id" value={doc.id} />
            <DetailRow label="title" value={doc.title} />
            <DetailRow label="source" value={doc.source} />
            <DetailRow label="domain" value={doc.domain} />
            {doc.document_type && <DetailRow label="document_type" value={doc.document_type} />}
            {ingestedAt && <DetailRow label="ingested_at" value={ingestedAt} />}
            <DetailRow label="source_path" value={doc.source_path} className="sm:col-span-2" />
          </CardContent>
        </Card>

        {/* Full JSON */}
        <div>
          <h2 className="text-xs uppercase tracking-wider text-zinc-500 mb-2">Raw document</h2>
          <Card interactive={false} className="overflow-hidden" data-testid="doc-detail-json">
            <JsonTree data={doc} collapseDepth={2} />
          </Card>
        </div>
      </div>
    </ScrollArea>
  );
}

function DetailRow({
  label,
  value,
  className = "",
}: {
  label: string;
  value: unknown;
  className?: string;
}) {
  return (
    <div className={`flex gap-2 min-w-0 ${className}`}>
      <span className="text-zinc-500 shrink-0 w-32">{label}</span>
      <span className="text-zinc-300 truncate">
        {value == null || value === "" ? <span className="text-zinc-600">—</span> : String(value)}
      </span>
    </div>
  );
}

function ErrorBlock({
  title,
  message,
  backTo,
}: {
  title: string;
  message?: string;
  backTo?: string;
}) {
  return (
    <div className="p-8" data-testid="doc-detail-error">
      <Card interactive={false} className="max-w-lg mx-auto mt-8">
        <CardContent className="flex flex-col items-center text-center py-8">
          <div className="rounded-full bg-red-950/50 p-3 mb-4">
            <AlertCircle className="h-6 w-6 text-red-400" />
          </div>
          <h3 className="text-sm font-medium text-zinc-200 mb-1">{title}</h3>
          {message && <p className="text-xs text-zinc-500 mb-4">{message}</p>}
          {backTo && (
            <Button asChild variant="outline" size="sm">
              <Link to={backTo}>Back</Link>
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
