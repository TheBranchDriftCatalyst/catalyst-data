import { useEffect } from "react";
import { Navigate, NavLink, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FileText, Newspaper, Tv2, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { fetchDomains } from "@/api/client";
import type { Domain } from "@/types/document";
import MediaList from "./media/MediaIngestList";
import CongressList from "./congress/CongressList";
import LeaksList from "./leaks/LeaksList";

/** Per-slug icon. Pure presentation; the registry endpoint owns slug+label
 *  but icons stay client-side since the backend has no business shipping
 *  lucide bindings. New domains get a generic FileText fallback below. */
const ICONS: Record<string, LucideIcon> = {
  media: Tv2,
  congress: FileText,
  leaks: Newspaper,
};

/** Per-slug list component. New domains plug in here once their wrapper
 *  ships under `pages/documents/<slug>/`. */
const LISTS: Record<string, React.ComponentType> = {
  media: MediaList,
  congress: CongressList,
  leaks: LeaksList,
};

const FALLBACK_DOMAIN = "media";

/** Top-level Documents page. The sub-tab row reads the live domain
 *  registry (`/viewer/api/domains`) so adding a new domain on the backend
 *  surfaces here automatically — no client-side mapping table. URL slug
 *  matches the API slug (e.g. `/documents/congress` ↔ `/viewer/api/congress/documents`). */
export default function Documents() {
  const { domain } = useParams<{ domain?: string }>();
  const navigate = useNavigate();

  const { data: domains, isLoading } = useQuery({
    queryKey: ["domains"],
    queryFn: fetchDomains,
    staleTime: 5 * 60_000,
  });

  // Redirect unknown / missing domain once the registry is loaded.
  useEffect(() => {
    if (!domains || isLoading) return;
    if (!domain) {
      navigate(`/documents/${FALLBACK_DOMAIN}`, { replace: true });
      return;
    }
    if (!domains.some((d) => d.slug === domain)) {
      navigate(`/documents/${FALLBACK_DOMAIN}`, { replace: true });
    }
  }, [domain, domains, isLoading, navigate]);

  if (!domain) return <Navigate to={`/documents/${FALLBACK_DOMAIN}`} replace />;

  const ListComponent = LISTS[domain];

  return (
    <div className="flex flex-col h-full">
      {/* Sub-tab row, registry-driven. Mirrors the visual language used
       *  by Player.tsx Tabs (h-9, border-b, px-3 triggers). URL is the
       *  source of truth via NavLink. */}
      <div
        data-testid="documents-subtabs"
        className="flex items-center h-9 border-b border-white/5 bg-surface-1 flex-shrink-0"
      >
        {(domains ?? []).map((d: Domain) => {
          const Icon = ICONS[d.slug] ?? FileText;
          return (
            <NavLink
              key={d.slug}
              to={`/documents/${d.slug}`}
              data-testid={`documents-subtab-${d.slug}`}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-1.5 px-3 h-full text-xs font-mono transition-colors border-b-2 -mb-px",
                  isActive
                    ? "border-cyan-400 text-cyan-300 bg-white/[0.03]"
                    : "border-transparent text-zinc-500 hover:text-zinc-300 hover:bg-white/[0.02]",
                )
              }
            >
              <Icon className="h-3.5 w-3.5" />
              {d.label}
            </NavLink>
          );
        })}
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {ListComponent ? <ListComponent /> : null}
      </div>
    </div>
  );
}
