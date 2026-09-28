import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export type SemanticColor =
  | "primary"
  | "secondary"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "muted"
  | "ai-accent";

export interface BadgeProps {
  label: ReactNode;
  color: SemanticColor;
  icon?: LucideIcon;
  size?: "sm" | "md";
}

const COLOR_CLASSES: Record<SemanticColor, string> = {
  primary: "bg-emerald-50 border border-emerald-200/80 text-emerald-800",
  secondary: "bg-stone-100 border border-stone-200 text-stone-700",
  success: "bg-emerald-50 border border-emerald-200/80 text-emerald-800",
  warning: "bg-amber-50 border border-amber-200/80 text-amber-800",
  danger: "bg-rose-50 border border-rose-200/80 text-rose-700 font-medium",
  info: "bg-stone-100 border border-stone-200 text-stone-700",
  muted: "bg-stone-100 border border-stone-200 text-stone-500",
  "ai-accent": "bg-emerald-50 border border-emerald-200/80 text-emerald-800",
};


/**
 * Base pill primitive underlying StatusBadge/PriorityBadge/SourceBadge
 * (DESIGN_SYSTEM.md §9). Color is never the only signal — a text label is
 * required, never color/icon alone.
 */
export function Badge({ label, color, icon: Icon, size = "md" }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-pill font-medium whitespace-nowrap",
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-xs",
        COLOR_CLASSES[color],
      )}
    >
      {Icon ? <Icon className="size-3" aria-hidden="true" /> : null}
      {label}
    </span>
  );
}
