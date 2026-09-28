import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  padding?: "none" | "sm" | "md" | "lg";
  clickable?: boolean;
}

/** Base surface primitive underlying every card variant with modern, clean styling. */
export function Card({
  padding = "md",
  clickable = false,
  className,
  children,
  ...rest
}: CardProps) {
  const paddingClasses = {
    none: "",
    sm: "p-4",
    md: "p-5 sm:p-6",
    lg: "p-6 sm:p-8",
  };

  return (
    <div
      className={cn(
        "rounded-xl border border-stone-200/80 bg-white shadow-sm transition-all duration-150",
        paddingClasses[padding],
        clickable && "cursor-pointer hover:bg-stone-50 hover:border-stone-300 hover:shadow-md",
        className,
      )}
      {...rest}
    >

      {children}
    </div>
  );
}
