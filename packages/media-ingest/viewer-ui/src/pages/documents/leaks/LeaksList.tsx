import { useQuery } from "@tanstack/react-query";
import { fetchDomainDocuments } from "@/api/client";
import { DomainDocumentList } from "../_shared/DomainDocumentList";

/** Open-leaks documents list. Mirror of CongressList — same generic
 *  factory pattern, different domain slug. */
export default function LeaksList() {
  const {
    data: documents = [],
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["documents", "leaks"],
    queryFn: () => fetchDomainDocuments("leaks"),
    staleTime: 30_000,
  });

  return (
    <DomainDocumentList
      domainSlug="leaks"
      heading="Open Leaks"
      documents={documents}
      isLoading={isLoading}
      isError={isError}
      error={error}
      onRefetch={refetch}
      getHref={(doc) => `/documents/leaks/${encodeURIComponent(doc.id)}`}
    />
  );
}
