import { useState } from "react";
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, TooltipProvider } from "@thebranchdriftcatalyst/catalyst-ui";
import Sidebar from "@/components/Sidebar";
import { TopNav } from "@/components/TopNav";
import DocumentList from "@/pages/DocumentList";
import PlayerPage from "@/pages/Player";
import EntityOverrides from "@/pages/EntityOverrides";
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

/** Pages that legitimately need the document-list sidebar context. The
 *  rest of the routes (Entity Overrides, S3 Explorer, Benchmarks,
 *  State Inspector) are global and shouldn't render the media sidebar
 *  at all — it just made them feel nested inside Media Explorer. */
const SIDEBAR_ROUTES = ["/", "/player"];

function shouldShowSidebar(pathname: string): boolean {
  return SIDEBAR_ROUTES.some(
    (p) => pathname === p || (p !== "/" && pathname.startsWith(p + "/")),
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
            <Route path="/" element={<DocumentList />} />
            <Route path="/player/:documentId" element={<PlayerPage />} />
            <Route path="/overrides" element={<EntityOverrides />} />
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
