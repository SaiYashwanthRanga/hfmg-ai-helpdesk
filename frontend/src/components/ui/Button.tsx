import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost" | "icon";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  icon?: ReactNode;
  "aria-label"?: string;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: "bg-primary text-primary-foreground hover:bg-primary-hover",
  secondary: "bg-transparent text-foreground border border-border hover:bg-card-hover",
  danger: "bg-danger text-danger-foreground hover:bg-danger-hover",
  ghost: "bg-transparent text-muted-foreground hover:bg-card-hover hover:text-foreground",
  icon: "bg-transparent text-muted-foreground hover:bg-card-hover hover:text-foreground",
};

/**
 * Base action primitive underlying every button in the product
 * (DESIGN_SYSTEM.md §10). `variant="icon"` renders a square hit area and
 * requires an `aria-label` since it carries no visible text.
 */
export function Button({
  variant = "primary",
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
        "inline-flex items-center justify-center gap-2 rounded-sm text-sm font-medium transition-colors duration-[120ms] ease-out",
        "disabled:cursor-not-allowed disabled:opacity-40",
        isIconOnly ? "size-10 shrink-0" : "h-10 px-4",
        VARIANT_CLASSES[variant],
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Loader2 className="size-4 animate-spin" aria-hidden="true" />
      ) : (
        <>
          {icon}
          {children}
        </>
      )}
    </button>
  );
}
