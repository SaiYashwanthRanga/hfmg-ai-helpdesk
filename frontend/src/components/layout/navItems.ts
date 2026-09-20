import { BarChart3, LayoutDashboard, Phone, Settings, Sparkles, Ticket } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface NavItemConfig {
  to: string;
  label: string;
  icon: LucideIcon;
  /** NavLink `end` — only the Dashboard route needs exact matching (DESIGN.md §4). */
  end?: boolean;
}

/** Six top-level items, flat, in the fixed order from DESIGN.md §4 — never reordered or grouped. */
export const NAV_ITEMS: NavItemConfig[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/tickets", label: "Tickets", icon: Ticket },
  { to: "/calls", label: "Calls", icon: Phone },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/ai-insights", label: "AI Insights", icon: Sparkles },
  { to: "/settings", label: "Settings", icon: Settings },
];
