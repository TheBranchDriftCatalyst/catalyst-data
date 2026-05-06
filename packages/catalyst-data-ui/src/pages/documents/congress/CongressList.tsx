import { useQuery } from "@tanstack/react-query";
import { fetchDomainDocuments } from "@/api/client";
import { DomainDocumentList } from "../_shared/DomainDocumentList";

/** Congress documents list. Backed by the generic factory at
 *  `/viewer/api/congress/documents`. Click routes to the generic detail
 *  page (`/documents/congress/<id>`) since congress docs don't have a
 *  media player. */
export default function CongressList() {
  const {
    data: documents = [],
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["documents", "congress"],
    queryFn: () => fetchDomainDocuments("congress"),
    staleTime: 30_000,
  });

  return (
    <DomainDocumentList
      domainSlug="congress"
      heading="Congress"
      documents={documents}
      isLoading={isLoading}
      isError={isError}
      error={error}
      onRefetch={refetch}
      getHref={(doc) => `/documents/congress/${encodeURIComponent(doc.id)}`}
    />
  );
}
