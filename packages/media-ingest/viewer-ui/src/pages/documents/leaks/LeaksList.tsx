import { useQuery } from "@tanstack/react-query";
import { fetchDomainDocuments } from "@/api/client";
import { DomainDocumentList } from "../_shared/DomainDocumentList";
import { ROUTE_TO_API_SLUG } from "../_shared/domains";

/** open-leaks documents list. Mirror of CongressList — same generic
 *  factory pattern, different domain slug. */
export default function LeaksList() {
  const apiSlug = ROUTE_TO_API_SLUG["open-leaks"]!;
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
      domainSlug="open-leaks"
      heading="Open Leaks"
      documents={documents}
      isLoading={isLoading}
      isError={isError}
      error={error}
      onRefetch={refetch}
      getHref={(doc) => `/documents/open-leaks/${encodeURIComponent(doc.id)}`}
    />
  );
}
