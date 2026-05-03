import { useQuery } from "@tanstack/react-query";
import { fetchDomainDocuments } from "@/api/client";
import { DomainDocumentList } from "../_shared/DomainDocumentList";
import { ROUTE_TO_API_SLUG } from "../_shared/domains";

/** congress-wtf documents list. Backed by the generic factory at
 *  `/viewer/api/congress/documents`. Click routes to the generic
 *  detail page (`/documents/congress-wtf/<id>`) since congress docs
 *  don't have a media player. */
export default function CongressList() {
  const apiSlug = ROUTE_TO_API_SLUG["congress-wtf"]!;
  const {
    data: documents = [],
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["documents", apiSlug],
    queryFn: () => fetchDomainDocuments(apiSlug),
    staleTime: 30_000,
  });

  return (
    <DomainDocumentList
      domainSlug="congress-wtf"
      heading="Congress"
      documents={documents}
      isLoading={isLoading}
      isError={isError}
      error={error}
      onRefetch={refetch}
      getHref={(doc) => `/documents/congress-wtf/${encodeURIComponent(doc.id)}`}
    />
  );
}
