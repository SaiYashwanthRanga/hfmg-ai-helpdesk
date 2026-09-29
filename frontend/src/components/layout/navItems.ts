import { BarChart3, FlaskConical, LayoutDashboard, Phone, Settings, Sparkles, Ticket } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useSettingsStatusQuery } from "../../api/settings";

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

/**
 * Developer/QA tool, not part of the six-item operational nav: shown only
 * when the backend reports ENABLE_VOICE_SIMULATOR on (never in production),
 * directly after Calls. See VOICE_SIMULATOR_DESIGN.md.
 */
export const VOICE_SIMULATOR_NAV_ITEM: NavItemConfig = {
  to: "/voice-simulator",
  label: "Call Simulator",
  icon: FlaskConical,
};

export function useNavItems(): NavItemConfig[] {
  const { data } = useSettingsStatusQuery();
  if (!data?.environment.voice_simulator_enabled) return NAV_ITEMS;
  const callsIndex = NAV_ITEMS.findIndex((item) => item.to === "/calls");
  return [...NAV_ITEMS.slice(0, callsIndex + 1), VOICE_SIMULATOR_NAV_ITEM, ...NAV_ITEMS.slice(callsIndex + 1)];
}
