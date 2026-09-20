import { createContext, useContext, useState, type ReactNode } from "react";

interface SidebarContextValue {
  mobileOpen: boolean;
  openMobile: () => void;
  closeMobile: () => void;
}

const SidebarContext = createContext<SidebarContextValue | null>(null);

/**
 * Shares mobile-overlay open state between Header's hamburger trigger and
 * Sidebar's full-screen overlay (DESIGN_SYSTEM.md §5.3) — the two live in
 * separate components, so this replaces prop drilling through AppShell.
 */
export function SidebarProvider({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <SidebarContext.Provider
      value={{
        mobileOpen,
        openMobile: () => setMobileOpen(true),
        closeMobile: () => setMobileOpen(false),
      }}
    >
      {children}
    </SidebarContext.Provider>
  );
}

export function useSidebar(): SidebarContextValue {
  const context = useContext(SidebarContext);
  if (!context) throw new Error("useSidebar must be used within SidebarProvider");
  return context;
}
