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
  primary: "bg-primary/16 text-primary",
  secondary: "bg-secondary/16 text-secondary-foreground",
  success: "bg-success/16 text-success",
  warning: "bg-warning/16 text-warning",
  danger: "bg-danger/16 text-danger",
  info: "bg-info/16 text-info",
  muted: "bg-muted-foreground/16 text-muted-foreground",
  "ai-accent": "bg-ai-subtle text-ai-accent",
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
