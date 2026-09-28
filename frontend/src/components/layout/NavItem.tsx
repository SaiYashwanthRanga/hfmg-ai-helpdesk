import { NavLink } from "react-router-dom";
import { cn } from "../../lib/cn";
import type { NavItemConfig } from "./navItems";

export interface NavItemProps {
  item: NavItemConfig;
  /**
   * Icon-only rail: label is visually hidden (screen-reader-only) below the
   * `xl` desktop breakpoint and a hover tooltip takes its place, then
   * becomes visible text at `xl`+ (DESIGN_SYSTEM.md §5.2). Used by the
   * persistent sidebar; the mobile overlay always passes `collapsed={false}`
   * since it only renders full-width, below the `md` breakpoint.
   */
  collapsed?: boolean;
  onNavigate?: () => void;
}

/**
 * Active state is indicated by color AND a leading marker — never color
 * alone (DESIGN.md §5, DESIGN_SYSTEM.md §2.5's accessibility rule applied
 * to navigation).
 */
export function NavItem({ item, collapsed = false, onNavigate }: NavItemProps) {
  const Icon = item.icon;

  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        cn(
          "group relative flex h-10 items-center gap-3 rounded-lg text-sm font-medium transition-all duration-150 outline-none focus-visible:ring-2 focus-visible:ring-emerald-800 focus-visible:ring-offset-1",
          collapsed ? "justify-center px-0 xl:justify-start xl:px-3" : "px-3",
          isActive
            ? "bg-stone-100 text-stone-900 font-semibold"
            : "text-stone-500 hover:bg-stone-50 hover:text-stone-900",
        )
      }
    >
      {({ isActive }) => (
        <>
          <span
            aria-hidden="true"
            className={cn(
              "absolute top-1/2 left-0 h-5 w-1 -translate-y-1/2 rounded-r-full bg-emerald-800 transition-all duration-150",
              isActive ? "opacity-100 scale-y-100" : "opacity-0 scale-y-50",
            )}
          />
          <Icon
            className={cn(
              "size-5 shrink-0 transition-transform duration-150 group-hover:scale-105",
              isActive ? "text-emerald-800" : "text-stone-400 group-hover:text-stone-600",
            )}
            aria-hidden="true"
          />
          <span className={collapsed ? "sr-only xl:not-sr-only tracking-normal" : "tracking-normal"}>{item.label}</span>
        </>
      )}


    </NavLink>
  );
}
