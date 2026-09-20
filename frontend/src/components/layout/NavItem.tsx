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
          "group relative flex h-10 items-center gap-3 rounded-sm text-sm font-medium transition-colors",
          collapsed ? "justify-center px-0 xl:justify-start xl:px-3" : "px-3",
          isActive ? "text-primary" : "text-muted-foreground hover:bg-card-hover hover:text-foreground",
        )
      }
    >
      {({ isActive }) => (
        <>
          <span
            aria-hidden="true"
            className={cn(
              "absolute top-1/2 left-0 h-5 w-0.5 -translate-y-1/2 rounded-full bg-primary transition-opacity",
              isActive ? "opacity-100" : "opacity-0",
            )}
          />
          <Icon className="size-5 shrink-0" aria-hidden="true" />
          <span className={collapsed ? "sr-only xl:not-sr-only" : ""}>{item.label}</span>
        </>
      )}
    </NavLink>
  );
}
