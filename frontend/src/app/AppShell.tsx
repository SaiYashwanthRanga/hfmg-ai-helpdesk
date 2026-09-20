import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Header } from "../components/layout/Header";
import { Sidebar } from "../components/layout/Sidebar";
import { queryClient } from "../lib/queryClient";
import { SidebarProvider } from "./SidebarContext";
import { ThemeProvider } from "./ThemeProvider";

/**
 * Top-level frame (FRONTEND_IMPLEMENTATION_PLAN.md Phase 1 + Phase 2).
 * Mounts theme/query providers, the persistent Header, the six-route
 * Sidebar, and a skip-to-content link that precedes Sidebar in tab order
 * (DESIGN_SYSTEM.md §20.2).
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <SidebarProvider>
          <a
            href="#main-content"
            className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-[200] focus:rounded-sm focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-foreground"
          >
            Skip to content
          </a>
          <div className="flex h-dvh flex-col overflow-hidden bg-background">
            <Header />
            {/*
              `min-h-0` overrides the flex default of `min-height: auto`,
              which otherwise lets this row grow to fit its tallest child's
              *content* height instead of being capped at the space left by
              Header — the classic cause of a flex child refusing to become
              a scroll container. With it, this row is pinned to exactly
              `h-dvh - header height`, and only #main-content below scrolls.
            */}
            <div className="flex min-h-0 flex-1">
              <Sidebar />
              <div id="main-content" tabIndex={-1} className="min-w-0 flex-1 overflow-y-auto focus:outline-none">
                {children}
              </div>
            </div>
          </div>
        </SidebarProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
