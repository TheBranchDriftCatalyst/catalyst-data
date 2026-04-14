import { useState } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, TooltipProvider } from "@thebranchdriftcatalyst/catalyst-ui";
import Sidebar from "@/components/Sidebar";
import DocumentList from "@/pages/DocumentList";
import PlayerPage from "@/pages/Player";
import EntityOverrides from "@/pages/EntityOverrides";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={200}>
          <BrowserRouter basename="/viewer">
            <div className="flex h-screen bg-surface-0 text-zinc-100 overflow-hidden">
              {/* Sidebar */}
              <Sidebar
                collapsed={sidebarCollapsed}
                onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
                className="flex-shrink-0"
              />

              {/* Main content */}
              <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
                <Routes>
                  <Route path="/" element={<DocumentList />} />
                  <Route path="/player/:documentId" element={<PlayerPage />} />
                  <Route path="/overrides" element={<EntityOverrides />} />
                </Routes>
              </main>
            </div>
          </BrowserRouter>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
