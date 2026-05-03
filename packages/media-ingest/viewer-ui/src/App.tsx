import { useState } from "react";
import { BrowserRouter, Routes, Route, useLocation, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, TooltipProvider } from "@thebranchdriftcatalyst/catalyst-ui";
import Sidebar from "@/components/Sidebar";
import { TopNav } from "@/components/TopNav";
import Documents from "@/pages/documents/Documents";
import DomainDocumentDetail from "@/pages/documents/_shared/DomainDocumentDetail";
import PlayerPage from "@/pages/Player";
import S3Explorer from "@/pages/S3Explorer";
import BenchmarkReport from "@/pages/BenchmarkReport";
import { StateInspector } from "@/pages/StateInspector";

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
    pathname.startsWith("/documents/media-ingest") ||
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
            <Route path="/" element={<Navigate to="/documents/media-ingest" replace />} />
            <Route path="/documents" element={<Navigate to="/documents/media-ingest" replace />} />
            <Route path="/documents/:domain" element={<Documents />} />
            {/* Generic per-doc detail page for non-media domains. Media-
             *  ingest still routes to /player/:id (the cards in
             *  MediaIngestList wire to the player directly, so this
             *  generic route is only hit by congress-wtf + open-leaks). */}
            <Route path="/documents/:domain/:id" element={<DomainDocumentDetail />} />
            <Route path="/player/:documentId" element={<PlayerPage />} />
            <Route path="/s3" element={<S3Explorer />} />
            <Route path="/benchmarks" element={<BenchmarkReport />} />
            <Route path="/benchmarks/state" element={<StateInspector />} />
          </Routes>
        </main>
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
          <BrowserRouter basename="/viewer">
            <AppShell
              sidebarCollapsed={sidebarCollapsed}
              onSidebarToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
            />
          </BrowserRouter>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
