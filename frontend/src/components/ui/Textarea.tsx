import type { TextareaHTMLAttributes } from "react";
import { useId } from "react";
import { cn } from "../../lib/cn";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
}

/** Multi-line text primitive (DESIGN_SYSTEM.md §11) — auto-grows via `rows`, capped by `max-h`. */
export function Textarea({ label, error, id, className, ...rest }: TextareaProps) {
  const generatedId = useId();
  const textareaId = id ?? generatedId;

  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label htmlFor={textareaId} className="text-sm font-medium text-foreground">
          {label}
        </label>
      ) : null}
      <textarea
        id={textareaId}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${textareaId}-error` : undefined}
        rows={4}
        className={cn(
          "min-h-24 max-h-80 w-full rounded-sm border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          "disabled:cursor-not-allowed disabled:opacity-40",
          error ? "border-danger" : "border-border",
          className,
        )}
        {...rest}
      />
      {error ? (
        <p id={`${textareaId}-error`} className="text-xs text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
