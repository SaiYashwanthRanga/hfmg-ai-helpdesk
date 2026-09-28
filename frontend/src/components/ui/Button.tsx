import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost" | "icon";
export type ButtonSize = "xs" | "sm" | "md" | "lg" | "icon";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
  "aria-label"?: string;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "bg-emerald-800 text-white hover:bg-emerald-900 active:bg-emerald-950 shadow-2xs",
  secondary: "bg-white text-stone-800 border border-stone-200/80 hover:bg-stone-50 hover:border-stone-300 shadow-2xs",
  danger: "bg-rose-700 text-white hover:bg-rose-800 active:bg-rose-900 shadow-2xs",
  ghost: "bg-transparent text-stone-600 hover:bg-stone-100/80 hover:text-stone-900",
  icon: "bg-transparent text-stone-500 hover:bg-stone-100 hover:text-stone-900",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  xs: "h-7 px-2.5 text-xs gap-1.5 rounded-md",
  sm: "h-8 px-3.5 text-xs gap-1.5 rounded-lg",
  md: "h-10 px-4 text-sm gap-2 rounded-lg",
  lg: "h-11 px-5 text-base gap-2.5 rounded-xl",
  icon: "size-8 p-0 rounded-lg shrink-0",
};

/**
 * Base action primitive with rock-solid flex centering and zero text clipping.
 */
export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled,
  icon,
  children,
  className,
  "aria-label": ariaLabel,
  ...rest
}: ButtonProps) {
  const isIconOnly = variant === "icon";

  if (isIconOnly && !ariaLabel) {
    // eslint-disable-next-line no-console
    console.warn("Button: icon-only buttons must have an aria-label (DESIGN_SYSTEM.md §8).");
  }

  return (
    <button
      type="button"
      aria-label={ariaLabel}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center whitespace-nowrap font-medium transition-all duration-150 leading-none select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-40",
        isIconOnly ? SIZE_CLASSES.icon : SIZE_CLASSES[size],
        VARIANT_CLASSES[variant],
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden="true" />
      ) : (
        <>
          {icon ? <span className="shrink-0">{icon}</span> : null}
          {children}
        </>
      )}
    </button>
  );
}
