import { useState } from "react";
import { BrowserRouter, Routes, Route, useLocation, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, TooltipProvider } from "@thebranchdriftcatalyst/catalyst-ui";
import Sidebar from "@/components/Sidebar";
import { TopNav } from "@/components/TopNav";
import DetailsPanel from "@/components/DetailsPanel";
import { SelectionProvider, useSelection } from "@/contexts/SelectionContext";
import { useEffect } from "react";
import Documents from "@/pages/documents/Documents";
import DomainDocumentDetail from "@/pages/documents/_shared/DomainDocumentDetail";
import PlayerPage from "@/pages/Player";
import S3Explorer from "@/pages/S3Explorer";
import BenchmarkReport from "@/pages/BenchmarkReport";
import { StateInspector } from "@/pages/StateInspector";
import { BenchmarkRunner } from "@/pages/BenchmarkRunner";
import BillList from "@/pages/bills/BillList";
import BillDetail from "@/pages/bills/BillDetail";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

/** Routes that render the media-ingest sidebar context. The sidebar is
 *  inherently media-ingest-specific (lists video/audio docs); other
 *  domains and global pages (S3 Explorer, Benchmarks, …) don't get it. */
function shouldShowSidebar(pathname: string): boolean {
  return (
    pathname.startsWith("/documents/media") ||
    pathname.startsWith("/player/")
  );
}

function AppShell({
  sidebarCollapsed,
  onSidebarToggle,
}: {
  sidebarCollapsed: boolean;
  onSidebarToggle: () => void;
}) {
  const { pathname } = useLocation();
  const showSidebar = shouldShowSidebar(pathname);
  const { clear, isOpen } = useSelection();

  // Esc closes the details panel. Lives at shell scope so it works no
  // matter which page produced the selection.
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") clear();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, clear]);

  // Selection state is route-scoped — switching pages clears it so a
  // stale assertion from /bills doesn't haunt the /player panel.
  useEffect(() => {
    clear();
  }, [pathname, clear]);

  return (
    <div className="flex flex-col h-screen bg-surface-0 text-zinc-100 overflow-hidden">
      <TopNav />
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {showSidebar && (
          <Sidebar
            collapsed={sidebarCollapsed}
            onToggle={onSidebarToggle}
            className="flex-shrink-0"
          />
        )}
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <Routes>
            {/* Root → default Documents domain. Forward-only — no /
             *  legacy redirect; smoke tests updated to land directly on
             *  /documents/<domain>. */}
            <Route path="/" element={<Navigate to="/documents/media" replace />} />
            <Route path="/documents" element={<Navigate to="/documents/media" replace />} />
            <Route path="/documents/:domain" element={<Documents />} />
            {/* Generic per-doc detail page for non-media domains. Media-
             *  ingest still routes to /player/:id (the cards in
             *  MediaIngestList wire to the player directly, so this
             *  generic route is only hit by congress-wtf + open-leaks). */}
            <Route path="/documents/:domain/:id" element={<DomainDocumentDetail />} />
            <Route path="/player/:documentId" element={<PlayerPage />} />
            {/* Congress bill viewer — partitioned-resource surface backed by
             *  /viewer/api/congress/bills*. Replaces the JSON-tree fallback
             *  for real (non-fixture) congress bills. */}
            <Route path="/bills" element={<BillList />} />
            <Route path="/bills/:partition" element={<BillDetail />} />
            <Route path="/s3" element={<S3Explorer />} />
            <Route path="/benchmarks" element={<BenchmarkReport />} />
            <Route path="/benchmarks/state" element={<StateInspector />} />
            <Route path="/benchmarks/runner" element={<BenchmarkRunner />} />
            {/* Backwards-compat redirect: /state-v2 was the V2 trial route. */}
            <Route
              path="/benchmarks/state-v2"
              element={<Navigate to="/benchmarks/state" replace />}
            />
          </Routes>
        </main>
        <DetailsPanel />
      </div>
    </div>
  );
}

export default function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={200}>
          <SelectionProvider>
            <BrowserRouter basename="/viewer">
              <AppShell
                sidebarCollapsed={sidebarCollapsed}
                onSidebarToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
              />
            </BrowserRouter>
          </SelectionProvider>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
