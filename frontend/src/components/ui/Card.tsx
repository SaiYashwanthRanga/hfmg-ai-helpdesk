import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  padding?: "sm" | "md";
  clickable?: boolean;
}

/** Base surface primitive underlying every card variant (DESIGN_SYSTEM.md §12). */
export function Card({ padding = "md", clickable = false, className, children, ...rest }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-card shadow-sm",
        padding === "md" ? "p-6" : "p-4",
        clickable && "cursor-pointer transition-shadow duration-150 hover:bg-card-hover hover:shadow-md",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}
