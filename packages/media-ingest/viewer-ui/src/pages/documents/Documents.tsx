import { Navigate, NavLink, useParams } from "react-router-dom";
import { FileText, Newspaper, Tv2 } from "lucide-react";
import { cn } from "@/lib/utils";
import MediaIngestList from "./media-ingest/MediaIngestList";
import CongressList from "./congress-wtf/CongressList";
import LeaksList from "./open-leaks/LeaksList";

type Domain = "media-ingest" | "congress-wtf" | "open-leaks";

interface DomainTab {
  domain: Domain;
  label: string;
  icon: typeof FileText;
}

const TABS: DomainTab[] = [
  { domain: "media-ingest", label: "media-ingest", icon: Tv2 },
  { domain: "congress-wtf", label: "congress-wtf", icon: FileText },
  { domain: "open-leaks", label: "open-leaks", icon: Newspaper },
];

const DEFAULT_DOMAIN: Domain = "media-ingest";

/** Top-level Documents page. Hosts a NavLink-row of per-domain sub-tabs
 *  whose URL is `/documents/<domain>` so deep-links and back/forward
 *  replay the user's domain choice. Each sub-tab renders its own list
 *  component — backend endpoints differ per domain. */
export default function Documents() {
  const { domain } = useParams<{ domain?: string }>();

  if (!domain) return <Navigate to={`/documents/${DEFAULT_DOMAIN}`} replace />;

  if (!TABS.some((t) => t.domain === domain)) {
    return <Navigate to={`/documents/${DEFAULT_DOMAIN}`} replace />;
  }

  const active = domain as Domain;

  return (
    <div className="flex flex-col h-full">
      {/* Sub-tab row. Mirrors the visual language used by Player.tsx Tabs
       *  (h-9, border-b, px-3 triggers) so it feels native. URL-driven via
       *  NavLink so /documents/<domain> is the source of truth. */}
      <div
        data-testid="documents-subtabs"
        className="flex items-center h-9 border-b border-white/5 bg-surface-1 flex-shrink-0"
      >
        {TABS.map(({ domain: d, label, icon: Icon }) => (
          <NavLink
            key={d}
            to={`/documents/${d}`}
            data-testid={`documents-subtab-${d}`}
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
            {label}
          </NavLink>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        {active === "media-ingest" && <MediaIngestList />}
        {active === "congress-wtf" && <CongressList />}
        {active === "open-leaks" && <LeaksList />}
      </div>
    </div>
  );
}
