import { useQuery } from "@tanstack/react-query";
import { fetchDocuments } from "@/api/client";
import { DomainDocumentList } from "../_shared/DomainDocumentList";

/** Media-ingest documents list. Thin wrapper over `<DomainDocumentList>`
 *  that fetches via the typed `fetchDocuments()` (returns `MediaDocument[]`,
 *  preserving the richer `media_url` / `thumbnail_url` shape so cards can
 *  render thumbnails). Click target is the player route since media docs
 *  always have an associated stream. */
export default function MediaIngestList() {
  const {
    data: documents = [],
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["documents"],
    queryFn: fetchDocuments,
    staleTime: 30_000,
  });

  return (
    <DomainDocumentList
      domainSlug="media-ingest"
      heading="Media Library"
      documents={documents}
      isLoading={isLoading}
      isError={isError}
      error={error}
      onRefetch={refetch}
      getHref={(doc) => `/player/${encodeURIComponent(doc.id)}`}
    />
  );
}
